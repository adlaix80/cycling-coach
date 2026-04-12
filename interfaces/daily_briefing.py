"""
daily_briefing.py
Orchestrates the morning automated briefing:
1. Pulls Strava activity from yesterday
2. Pulls today's Garmin metrics
3. Updates activity log and PMC
4. Generates coaching briefing via Claude
5. Sends to Telegram
"""

import os
from datetime import date
from dotenv import load_dotenv
from clients.strava_client import StravaClient
from clients.garmin_client import GarminClient
from engine.training_state import TrainingState
from engine.coach_engine import CoachEngine

load_dotenv()


def run_daily_briefing(training_state: TrainingState, send_fn=None):
    """
    Main daily briefing function.
    send_fn: callable(text) that delivers the message (e.g. Telegram send)
    """
    print(f"[Briefing] Starting daily briefing for {date.today().isoformat()}")

    # Avoid duplicate briefings on the same day
    if training_state.state.get("last_briefing_date") == date.today().isoformat():
        print("[Briefing] Already sent today, skipping.")
        return

    ftp = training_state.get_current_ftp() or 200  # fallback default

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
            print(f"[Briefing] Strava: pulled activity '{yesterday_activity.get('name')}'")
        else:
            print("[Briefing] Strava: no activity yesterday")

        recent_summary = strava.get_28_day_summary(ftp)
        # Log all recent activities to ensure PMC is current
        for a in recent_summary.get("activities", []):
            training_state.log_activity(a)

    except Exception as e:
        print(f"[Briefing] Strava error: {e}")

    # ── Pull Garmin ────────────────────────────────────────────────────────────
    garmin_metrics = None
    try:
        garmin = GarminClient()
        garmin_metrics = garmin.get_todays_metrics()

        # Update FTP and VO2max if Garmin has new values
        if garmin_metrics.get("ftp_estimate_w"):
            new_ftp = garmin_metrics["ftp_estimate_w"]
            current_ftp = training_state.get_current_ftp()
            if not current_ftp or abs(new_ftp - current_ftp) > 2:
                training_state.record_ftp(new_ftp, source="garmin")
                print(f"[Briefing] FTP updated: {new_ftp}W")

        if garmin_metrics.get("vo2max"):
            training_state.record_vo2max(garmin_metrics["vo2max"], source="garmin")

        print(f"[Briefing] Garmin: readiness {garmin_metrics.get('training_readiness_score')}, "
              f"HRV {garmin_metrics.get('hrv_status')}")

    except Exception as e:
        print(f"[Briefing] Garmin error: {e}")

    # ── Recalculate PMC ────────────────────────────────────────────────────────
    try:
        training_state.recalculate_pmc()
        pmc = training_state.get_pmc_summary()
        print(f"[Briefing] PMC: CTL={pmc['ctl']}, ATL={pmc['atl']}, TSB={pmc['tsb']}")
    except Exception as e:
        print(f"[Briefing] PMC calculation error: {e}")

    # ── Generate Briefing ──────────────────────────────────────────────────────
    try:
        engine = CoachEngine(training_state)
        briefing = engine.generate_daily_briefing(
            yesterday_activity=yesterday_activity,
            garmin_metrics=garmin_metrics,
            recent_summary=recent_summary,
        )
        print("[Briefing] Briefing generated successfully")
    except Exception as e:
        print(f"[Briefing] Coach engine error: {e}")
        briefing = f"⚠️ Morning briefing failed to generate: {e}\nCheck logs for details."

    # ── Deliver ────────────────────────────────────────────────────────────────
    header = f"🚴 *Good morning — Daily Briefing {date.today().strftime('%A, %d %b')}*\n\n"
    message = header + briefing

    if send_fn:
        send_fn(message)
    else:
        print("\n" + "="*60)
        print(message)
        print("="*60)

    training_state.state["last_briefing_date"] = date.today().isoformat()
    training_state.save()
    print("[Briefing] Complete.")
