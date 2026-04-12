"""
progress_tracker.py
Generates monthly progress reports tracking FTP and VO2max trends,
training load progression, and goal proximity.
Called on the 1st of each month by the scheduler.
"""

import logging
from datetime import date, timedelta
from engine.training_state import TrainingState
from engine.coach_engine import CoachEngine

logger = logging.getLogger(__name__)


def run_monthly_progress_report(training_state: TrainingState, send_fn=None):
    """
    Generate and deliver a monthly progress report.
    Runs on the 1st of each month.
    """
    logger.info("[Progress] Generating monthly report")

    ftp_trend = training_state.get_ftp_trend()
    vo2_history = training_state.state.get("vo2max_history", [])
    pmc = training_state.get_pmc_summary()
    goals = training_state.state.get("goals", {})
    athlete = training_state.state.get("athlete", {})

    # ── VO2max trend ───────────────────────────────────────────────────────────
    vo2_trend = _calculate_vo2_trend(vo2_history)

    # ── Goal proximity ─────────────────────────────────────────────────────────
    goal_assessment = _assess_goal_proximity(goals, ftp_trend, athlete)

    # ── 28-day training load ───────────────────────────────────────────────────
    recent_activities = training_state.get_activities_last_n_days(28)
    total_hours = sum(a.get("duration_min", 0) for a in recent_activities) / 60
    total_tss = sum(a.get("tss", 0) or 0 for a in recent_activities)

    # ── Generate narrative ─────────────────────────────────────────────────────
    engine = CoachEngine(training_state)
    context = f"""Generate a monthly progress report for this cyclist.

MONTH: {date.today().strftime('%B %Y')}

FTP PROGRESS:
- Current: {ftp_trend.get('current')}W
- 4-week change: {ftp_trend.get('change_4w')}W
- 12-week change: {ftp_trend.get('change_12w')}W
- W/kg: {round(ftp_trend['current'] / athlete['weight_kg'], 2) if ftp_trend.get('current') and athlete.get('weight_kg') else 'N/A'}

VO2MAX PROGRESS:
- Current: {vo2_trend.get('current')} ml/kg/min
- 4-week change: {vo2_trend.get('change_4w')}
- 12-week change: {vo2_trend.get('change_12w')}

TRAINING LOAD (last 28 days):
- Total hours: {round(total_hours, 1)}h
- Total TSS: {total_tss}
- CTL (fitness): {pmc.get('ctl')}
- ATL (fatigue): {pmc.get('atl')}

PRIMARY GOAL: {goals.get('primary_goal')} by {goals.get('primary_goal_date')}
Goal assessment: {goal_assessment}

Please provide:
1. Honest assessment of monthly progress
2. Whether current trajectory puts the athlete on track for their primary goal
3. The one area showing the most improvement
4. The one area needing the most focus next month
5. Any adjustments recommended for the upcoming training block

This is a monthly reflection — be thorough but clear."""

    try:
        narrative = engine.chat(context)
    except Exception as e:
        logger.error(f"[Progress] Narrative error: {e}")
        narrative = "Could not generate narrative this month."

    message = _format_progress_message(
        ftp_trend=ftp_trend,
        vo2_trend=vo2_trend,
        pmc=pmc,
        total_hours=total_hours,
        total_tss=total_tss,
        narrative=narrative,
        goals=goals,
    )

    if send_fn:
        send_fn(message)
    else:
        print(message)

    logger.info("[Progress] Monthly report complete.")


def _calculate_vo2_trend(vo2_history: list) -> dict:
    """Calculate VO2max change over 4 and 12 weeks."""
    if not vo2_history:
        return {"current": None, "change_4w": None, "change_12w": None}

    sorted_h = sorted(vo2_history, key=lambda x: x["date"])
    current = sorted_h[-1]["vo2max"]
    today = date.today()

    def find_nearest(days_ago):
        target = (today - timedelta(days=days_ago)).isoformat()
        past = [h for h in sorted_h if h["date"] <= target]
        return past[-1]["vo2max"] if past else None

    past_4w = find_nearest(28)
    past_12w = find_nearest(84)
    return {
        "current": current,
        "change_4w": round(current - past_4w, 1) if past_4w else None,
        "change_12w": round(current - past_12w, 1) if past_12w else None,
    }


def _assess_goal_proximity(goals: dict, ftp_trend: dict, athlete: dict) -> str:
    """Simple heuristic assessment of whether the athlete is on track."""
    primary = goals.get("primary_goal", "")
    target_date_str = goals.get("primary_goal_date")
    if not target_date_str:
        return "No target date set"

    try:
        target_date = date.fromisoformat(target_date_str)
    except ValueError:
        return "Invalid target date"

    days_remaining = (target_date - date.today()).days
    if days_remaining < 0:
        return "Goal date has passed"
    if days_remaining < 30:
        return f"Goal date in {days_remaining} days — taper phase"
    if days_remaining < 90:
        return f"Goal date in {days_remaining} days — peak phase approaching"
    return f"Goal date in {days_remaining} days — building phase"


def _format_progress_message(ftp_trend, vo2_trend, pmc, total_hours, total_tss,
                               narrative, goals) -> str:
    """Format the monthly progress report for Telegram."""
    ftp_arrow = "↑" if (ftp_trend.get("change_4w") or 0) > 0 else "↓"
    vo2_arrow = "↑" if (vo2_trend.get("change_4w") or 0) > 0 else "↓"

    lines = [
        f"🗓️ *Monthly Progress Report — {date.today().strftime('%B %Y')}*\n",
        "━" * 30,
        "",
        "💪 *Physiological Progress*",
        f"  FTP: *{ftp_trend.get('current')}W* {ftp_arrow}{abs(ftp_trend.get('change_4w') or 0)}W (4w) "
        f"| {ftp_arrow}{abs(ftp_trend.get('change_12w') or 0)}W (12w)",
        f"  VO2max: *{vo2_trend.get('current')} ml/kg/min* {vo2_arrow}{abs(vo2_trend.get('change_4w') or 0)} (4w)",
        "",
        "📊 *Training Load (28 days)*",
        f"  Hours: {round(total_hours, 1)}h | TSS: {total_tss}",
        f"  CTL: {pmc.get('ctl')} | TSB: {pmc.get('tsb')}",
        "",
        "🎯 *Primary Goal*",
        f"  {goals.get('primary_goal', '—')} by {goals.get('primary_goal_date', '—')}",
        "",
        "━" * 30,
        "",
        narrative,
    ]
    return "\n".join(lines)
