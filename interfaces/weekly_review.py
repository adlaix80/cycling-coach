"""
weekly_review.py
Generates a comprehensive weekly review every Sunday evening:
- Compliance report (planned vs completed)
- TSS achieved vs target
- FTP and VO2max trend
- Coach commentary on the week
- Preview of next week's block
- Triggers week advancement (calls plan_builder.advance_week)
"""

import logging
from datetime import date, timedelta
from engine.training_state import TrainingState
from engine.coach_engine import CoachEngine
from engine.plan_builder import PlanBuilder
from clients.strava_client import StravaClient

logger = logging.getLogger(__name__)


def run_weekly_review(training_state: TrainingState, send_fn=None):
    """
    Main weekly review function. Runs Sunday evening.
    send_fn: callable(text) for Telegram delivery.
    """
    logger.info("[Weekly Review] Starting Sunday review")

    ftp = training_state.get_current_ftp() or 200
    plan_builder = PlanBuilder(training_state)
    engine = CoachEngine(training_state)

    # ── Pull week's Strava data ────────────────────────────────────────────────
    week_activities = []
    try:
        strava = StravaClient()
        week_activities = strava.get_recent_activities(days=7)
        for a in week_activities:
            a["tss"] = strava.calculate_tss(a, ftp)
            training_state.log_activity(a)
        logger.info(f"[Weekly Review] {len(week_activities)} activities this week")
    except Exception as e:
        logger.error(f"[Weekly Review] Strava error: {e}")

    # ── Use last saved Garmin metrics (from manual entry) ──────────────────────
    garmin_metrics = training_state.state.get("last_garmin_metrics")

    # ── Recalculate PMC ────────────────────────────────────────────────────────
    training_state.recalculate_pmc()

    # ── Match activities to planned sessions ───────────────────────────────────
    for activity in week_activities:
        matched = plan_builder.match_completed_to_planned(activity)
        if matched:
            plan_builder.mark_session_complete(
                matched["id"],
                actual_tss=activity.get("tss")
            )

    # ── Compliance report ──────────────────────────────────────────────────────
    compliance = plan_builder.get_compliance_report()

    # ── Check for upcoming A events requiring taper ────────────────────────────
    upcoming_event = plan_builder.check_upcoming_events()

    # ── Advance to next week ───────────────────────────────────────────────────
    if upcoming_event and upcoming_event.get("days_out", 99) <= 7:
        logger.info(f"[Weekly Review] Race week taper — {upcoming_event['name']}")
        next_week_sessions = plan_builder.insert_peak_week(upcoming_event["date"])
        next_week_note = f"🏁 *Race week! Taper plan loaded for {upcoming_event['name']}.*"
    else:
        next_week_sessions = plan_builder.advance_week()
        next_week_note = None

    # ── Generate review narrative via Claude ───────────────────────────────────
    pmc = training_state.get_pmc_summary()
    ftp_trend = training_state.get_ftp_trend()
    block = training_state.state["current_block"]

    context = _build_review_context(
        week_activities=week_activities,
        compliance=compliance,
        pmc=pmc,
        ftp_trend=ftp_trend,
        garmin_metrics=garmin_metrics,
        block=block,
        next_week_sessions=next_week_sessions,
        upcoming_event=upcoming_event,
    )

    try:
        review_text = engine.chat(context)
    except Exception as e:
        logger.error(f"[Weekly Review] Coach engine error: {e}")
        review_text = "⚠️ Could not generate coach commentary this week."

    # ── Format and send ────────────────────────────────────────────────────────
    message = _format_review_message(
        compliance=compliance,
        pmc=pmc,
        ftp_trend=ftp_trend,
        block=block,
        next_week_sessions=next_week_sessions,
        review_text=review_text,
        next_week_note=next_week_note,
    )

    if send_fn:
        send_fn(message)
    else:
        print("\n" + "=" * 60)
        print(message)
        print("=" * 60)

    logger.info("[Weekly Review] Complete.")


def _build_review_context(week_activities, compliance, pmc, ftp_trend,
                           garmin_metrics, block, next_week_sessions, upcoming_event) -> str:
    """Build the prompt context for the weekly coach narrative."""
    activity_lines = []
    for a in week_activities:
        activity_lines.append(
            f"  - {a['date']} {a['name']}: {a['duration_min']}min, "
            f"NP={a.get('weighted_avg_power_w')}W, TSS={a.get('tss')}"
        )

    next_session_lines = []
    for s in (next_week_sessions or [])[:3]:  # preview first 3
        next_session_lines.append(f"  - {s['day']}: {s['title']} ({s['duration_min']}min, ~{s['tss_estimate']} TSS)")

    return f"""Please provide the weekly review coaching narrative.

WEEK SUMMARY:
- Sessions completed: {compliance['sessions_completed']}/{compliance['sessions_planned']} ({compliance['compliance_pct']}%)
- TSS achieved: {compliance['actual_tss']} / {compliance['planned_tss']} planned ({compliance['tss_compliance_pct']}%)
- Activities this week:
{chr(10).join(activity_lines) if activity_lines else '  None recorded'}

FITNESS STATUS:
- CTL: {pmc.get('ctl')} | ATL: {pmc.get('atl')} | TSB: {pmc.get('tsb')}
- FTP: {ftp_trend.get('current')}W (4w change: {ftp_trend.get('change_4w')}W)
- Garmin readiness: {garmin_metrics.get('training_readiness_score') if garmin_metrics else 'N/A'}
- HRV: {garmin_metrics.get('hrv_status') if garmin_metrics else 'N/A'}

CURRENT BLOCK: {block.get('type')} week {block.get('week_number')} of {block.get('total_weeks')}

NEXT WEEK PREVIEW:
{chr(10).join(next_session_lines) if next_session_lines else '  TBC'}
{'UPCOMING EVENT: ' + upcoming_event['name'] + ' in ' + str(upcoming_event['days_out']) + ' days!' if upcoming_event else ''}

Please provide:
1. A qualitative assessment of the week — what went well, what didn't
2. Key physiological insight (CTL/ATL trend, FTP direction)
3. One specific focus for next week
4. Any adjustments to the training approach going forward

Keep it motivating but honest. This is a Sunday evening message so the athlete is winding down."""


def _format_review_message(compliance, pmc, ftp_trend, block, next_week_sessions,
                             review_text, next_week_note) -> str:
    """Format the full weekly review Telegram message."""
    compliance_emoji = "✅" if compliance["compliance_pct"] >= 80 else "⚠️" if compliance["compliance_pct"] >= 50 else "❌"
    tsb_emoji = "🟢" if (pmc.get("tsb") or 0) > 0 else "🟡" if (pmc.get("tsb") or 0) > -20 else "🔴"

    lines = [
        f"📊 *Weekly Review — w/e {date.today().strftime('%d %b %Y')}*\n",
        f"{compliance_emoji} *Compliance:* {compliance['sessions_completed']}/{compliance['sessions_planned']} sessions "
        f"| {compliance['actual_tss']}/{compliance['planned_tss']} TSS\n",
        f"{tsb_emoji} *Fitness:* CTL {pmc.get('ctl')} | ATL {pmc.get('atl')} | TSB {pmc.get('tsb')}",
        f"📈 *FTP:* {ftp_trend.get('current')}W "
        f"({'↑' if (ftp_trend.get('change_4w') or 0) > 0 else '↓'}"
        f"{abs(ftp_trend.get('change_4w') or 0)}W vs 4w)\n",
        "─" * 30,
        "",
        review_text,
        "",
        "─" * 30,
        f"\n📅 *Next Week — {block.get('type', '').title()} Block, Week {block.get('week_number')}*\n",
    ]

    if next_week_note:
        lines.append(next_week_note + "\n")

    if next_week_sessions:
        for s in next_week_sessions:
            type_emoji = {
                "rest": "😴", "recovery": "🔵", "endurance": "🟢",
                "tempo": "🟡", "sweet_spot": "🟠", "threshold": "🔴",
                "vo2max": "🟣", "anaerobic": "⚫",
            }.get(s.get("type", ""), "⚪")
            lines.append(
                f"{type_emoji} *{s['day']}*: {s['title']} "
                f"({s.get('duration_min')}min, ~{s.get('tss_estimate')} TSS)"
            )

    return "\n".join(lines)
