"""
daily_briefing.py
Orchestrates the morning automated briefing using Strava only.
Garmin metrics are sourced from the last manual entry stored in state.
"""

import os
import logging
from datetime import date
from dotenv import load_dotenv
from clients.strava_client import StravaClient
from engine.training_state import TrainingState
from engine.coach_engine import CoachEngine

load_dotenv()
logger = logging.getLogger(__name__)


def run_daily_briefing(training_state: TrainingState, send_fn=None):
    logger.info(f"[Briefing] Starting daily briefing for {date.today().isoformat()}")

    if training_state.state.get("last_briefing_date") == date.today().isoformat():
        logger.info("[Briefing] Already sent today, skipping.")
        return

    ftp = training_state.get_current_ftp() or 200

    # ── Pull Strava ────────────────────────────────────────────────────────────
    yesterday_activity = None
    recent_summary = None
    try:
        strava = StravaClient()
        yesterday_activity = strava.get_yesterdays_activity()
        if yesterday_activity:
            tss = strava.calculate_tss(yesterday_activity, ftp)
            yesterday_activity["tss"] = tss
            if tss and yesterday_activity.get("weighted_avg_power_w"):
                yesterday_activity["intensity_factor"] = round(
                    yesterday_activity["weighted_avg_power_w"] / ftp, 3
                )
            training_state.log_activity(yesterday_activity)
            logger.info(f"[Briefing] Strava: '{yesterday_activity.get('name')}'")
        recent_summary = strava.get_28_day_summary(ftp)
        for a in recent_summary.get("activities", []):
            training_state.log_activity(a)
    except Exception as e:
        logger.error(f"[Briefing] Strava error: {e}")

    # ── Use last saved Garmin metrics (from manual entry) ──────────────────────
    garmin_metrics = training_state.state.get("last_garmin_metrics")
    if garmin_metrics:
        logger.info(f"[Briefing] Using manual Garmin data from {garmin_metrics.get('date')}")
    else:
        logger.info("[Briefing] No Garmin data available")

    # ── Recalculate PMC ────────────────────────────────────────────────────────
    try:
        training_state.recalculate_pmc()
        pmc = training_state.get_pmc_summary()
        logger.info(f"[Briefing] PMC: CTL={pmc['ctl']}, ATL={pmc['atl']}, TSB={pmc['tsb']}")
    except Exception as e:
        logger.error(f"[Briefing] PMC error: {e}")

    # ── Generate briefing ──────────────────────────────────────────────────────
    try:
        engine = CoachEngine(training_state)
        briefing = engine.generate_daily_briefing(
            yesterday_activity=yesterday_activity,
            garmin_metrics=garmin_metrics,
            recent_summary=recent_summary,
        )
    except Exception as e:
        logger.error(f"[Briefing] Coach engine error: {e}")
        briefing = f"⚠️ Briefing generation failed: {e}"

    header = f"🚴 *Good morning — Daily Briefing {date.today().strftime('%A, %d %b')}*\n\n"
    message = header + briefing

    if send_fn:
        send_fn(message)
    else:
        print(message)

    training_state.state["last_briefing_date"] = date.today().isoformat()
    training_state.save()
    logger.info("[Briefing] Complete.")
