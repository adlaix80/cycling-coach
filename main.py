"""
main.py
Entry point for the AI Cycling Coach.

Runs three concurrent processes:
1. Daily briefing — every morning at 07:00
2. Weekly review — every Sunday at 20:00
3. Monthly progress report — 1st of each month at 09:00
4. Telegram bot — always-on conversational interface

Usage:
  python main.py              # Normal run
  python main.py --onboard    # Force onboarding flow
  python main.py --briefing   # Trigger manual briefing now
  python main.py --review     # Trigger manual weekly review now
  python main.py --progress   # Trigger manual monthly report now
"""

import sys
import os
import logging
import yaml
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from engine.training_state import TrainingState
from interfaces.daily_briefing import run_daily_briefing
from interfaces.weekly_review import run_weekly_review
from interfaces.progress_tracker import run_monthly_progress_report
from interfaces.telegram_bot import TelegramBot

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

os.makedirs("logs", exist_ok=True)
os.makedirs("data/dossier", exist_ok=True)


def load_config() -> dict:
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)


def main():
    config = load_config()
    training_state = TrainingState()
    bot = TelegramBot(training_state)

    args = sys.argv[1:]

    # ── Manual triggers ────────────────────────────────────────────────────────
    if "--briefing" in args:
        logger.info("Manual briefing triggered")
        run_daily_briefing(training_state=training_state, send_fn=bot.send_message_sync)
        return

    if "--review" in args:
        logger.info("Manual weekly review triggered")
        run_weekly_review(training_state=training_state, send_fn=bot.send_message_sync)
        return

    if "--progress" in args:
        logger.info("Manual monthly report triggered")
        run_monthly_progress_report(training_state=training_state, send_fn=bot.send_message_sync)
        return

    # ── Force onboarding ──────────────────────────────────────────────────────
    if "--onboard" in args:
        logger.info("Forcing onboarding reset")
        training_state.state["onboarding_complete"] = False
        training_state.save()

    # ── Credential check ──────────────────────────────────────────────────────
    required_env = [
        "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN",
        "STRAVA_CLIENT_ID", "STRAVA_REFRESH_TOKEN",
        "GARMIN_EMAIL", "GARMIN_PASSWORD",
    ]
    missing = [k for k in required_env if not os.getenv(k)]
    if missing:
        logger.error(f"Missing credentials: {', '.join(missing)}")
        logger.error("Copy .env.example to .env and fill in all values.")
        logger.error("For Strava OAuth, run: python setup_strava.py first.")
        sys.exit(1)

    # ── Scheduler ─────────────────────────────────────────────────────────────
    briefing_time = config["coaching"].get("briefing_time", "07:00")
    bh, bm = map(int, briefing_time.split(":"))

    scheduler = BackgroundScheduler(timezone="Europe/Zurich")

    # Daily briefing
    scheduler.add_job(
        func=lambda: run_daily_briefing(
            training_state=training_state,
            send_fn=bot.send_message_sync,
        ),
        trigger="cron",
        hour=bh, minute=bm,
        id="daily_briefing",
        name="Daily Briefing",
        replace_existing=True,
    )

    # Weekly review — Sunday 20:00
    scheduler.add_job(
        func=lambda: run_weekly_review(
            training_state=training_state,
            send_fn=bot.send_message_sync,
        ),
        trigger="cron",
        day_of_week="sun",
        hour=20, minute=0,
        id="weekly_review",
        name="Weekly Review",
        replace_existing=True,
    )

    # Week advancement — Monday 00:05 (just after midnight)
    scheduler.add_job(
        func=lambda: _advance_week_if_needed(training_state),
        trigger="cron",
        day_of_week="mon",
        hour=0, minute=5,
        id="week_advance",
        name="Week Advancement",
        replace_existing=True,
    )

    # Monthly progress report — 1st of month, 09:00
    scheduler.add_job(
        func=lambda: run_monthly_progress_report(
            training_state=training_state,
            send_fn=bot.send_message_sync,
        ),
        trigger="cron",
        day=1, hour=9, minute=0,
        id="monthly_progress",
        name="Monthly Progress Report",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        f"Scheduler started — daily {briefing_time}, "
        f"weekly Sun 20:00, monthly 1st 09:00 (Europe/Zurich)"
    )

    # ── Start Telegram bot ─────────────────────────────────────────────────────
    logger.info("Starting Telegram bot...")
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        scheduler.shutdown()
        logger.info("Stopped.")


def _advance_week_if_needed(training_state: TrainingState):
    """Advance the training week on Monday if the block has sessions."""
    from engine.plan_builder import PlanBuilder
    block = training_state.state.get("current_block", {})
    if block.get("sessions"):
        pb = PlanBuilder(training_state)
        pb.advance_week()
        logger.info("[Scheduler] Week advanced")


if __name__ == "__main__":
    main()
