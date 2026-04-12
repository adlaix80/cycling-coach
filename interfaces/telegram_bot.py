"""
telegram_bot.py
Telegram bot providing:
- Onboarding conversation flow
- Daily briefing delivery
- Conversational coaching (ask questions, update goals/availability)
- Dossier document upload processing
- Training status commands
"""

import os
import json
import logging
from datetime import date
from pathlib import Path
from dotenv import load_dotenv, set_key

from telegram import Update, Document
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from engine.training_state import TrainingState
from engine.coach_engine import CoachEngine
from engine.dossier_parser import parse_dossier, format_extraction_for_confirmation

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Onboarding conversation store (in-memory per session)
_onboarding_sessions: dict[int, list[dict]] = {}


class TelegramBot:
    def __init__(self, training_state: TrainingState):
        self.state = training_state
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.app = Application.builder().token(self.token).build()
        self._register_handlers()
        self._pending_dossier_confirmation: dict | None = None

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("plan", self.cmd_plan))
        self.app.add_handler(CommandHandler("metrics", self.cmd_metrics))
        self.app.add_handler(CommandHandler("update", self.cmd_update))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    # ─── Utility ──────────────────────────────────────────────────────────────

    async def _send(self, update: Update, text: str, parse_mode: str = "Markdown"):
        """Send a message, splitting if over Telegram's 4096 char limit."""
        max_len = 4000
        for i in range(0, len(text), max_len):
            await update.message.reply_text(
                text[i:i + max_len],
                parse_mode=parse_mode
            )

    def _save_chat_id(self, chat_id: int):
        """Persist the chat ID to .env for use by the scheduler."""
        env_path = ".env"
        if os.path.exists(env_path):
            set_key(env_path, "TELEGRAM_CHAT_ID", str(chat_id))

    # ─── Commands ─────────────────────────────────────────────────────────────

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self._save_chat_id(chat_id)

        if not self.state.is_onboarded():
            await self._send(update,
                "👋 *Welcome to your AI Cycling Coach!*\n\n"
                "I'm going to help you train smarter. Before we start, I need to build your athlete profile.\n\n"
                "If you have a training dossier (PDF, Word, or text file), send it to me now and I'll extract your details automatically.\n\n"
                "Or type *skip* and I'll ask you a few questions instead."
            )
            _onboarding_sessions[chat_id] = []
        else:
            name = self.state.state["athlete"].get("name", "")
            await self._send(update,
                f"👋 Welcome back{', ' + name if name else ''}! Your coaching session is active.\n\n"
                "Use /help to see available commands, or just chat with me."
            )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pmc = self.state.get_pmc_summary()
        ftp_trend = self.state.get_ftp_trend()
        block = self.state.state["current_block"]

        text = (
            f"📊 *Training Status — {date.today().strftime('%d %b %Y')}*\n\n"
            f"*Fitness (CTL):* {pmc.get('ctl', '—')}\n"
            f"*Fatigue (ATL):* {pmc.get('atl', '—')}\n"
            f"*Form (TSB):* {pmc.get('tsb', '—')}\n\n"
            f"*FTP:* {ftp_trend.get('current', '—')}W "
            f"({'↑' if (ftp_trend.get('change_4w') or 0) > 0 else '↓'}"
            f"{abs(ftp_trend.get('change_4w') or 0)}W vs 4w ago)\n\n"
            f"*Current block:* {block.get('type', '—')} "
            f"(Week {block.get('week_number', '—')} of {block.get('total_weeks', '—')})\n"
            f"*Target weekly TSS:* {block.get('target_tss_week', '—')}"
        )
        await self._send(update, text)

    async def cmd_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        sessions = self.state.get_this_weeks_sessions()
        if not sessions:
            await self._send(update, "No sessions planned for this week yet. Ask me to generate a training block!")
            return

        lines = [f"📅 *This Week's Training Plan*\n"]
        for s in sessions:
            status_emoji = "✅" if s.get("status") == "complete" else "🔵"
            lines.append(
                f"{status_emoji} *{s['day']}* — {s['title']}\n"
                f"  {s.get('duration_min')}min | {s.get('type')} | ~{s.get('tss_estimate')} TSS\n"
                f"  _{s.get('description', '')[:120]}..._\n"
            )
        await self._send(update, "\n".join(lines))

    async def cmd_metrics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        a = self.state.state["athlete"]
        ftp_trend = self.state.get_ftp_trend()
        vo2_history = self.state.state.get("vo2max_history", [])
        latest_vo2 = vo2_history[-1] if vo2_history else {}

        text = (
            f"📈 *Physiological Metrics*\n\n"
            f"*FTP:* {ftp_trend.get('current', '—')}W\n"
            f"  4-week change: {ftp_trend.get('change_4w', '—')}W\n"
            f"  12-week change: {ftp_trend.get('change_12w', '—')}W\n\n"
            f"*VO2max:* {latest_vo2.get('vo2max', '—')} ml/kg/min\n"
            f"  (as of {latest_vo2.get('date', '—')})\n\n"
            f"*W/kg:* "
            f"{round(ftp_trend['current']/a['weight_kg'], 2) if ftp_trend.get('current') and a.get('weight_kg') else '—'}"
        )
        await self._send(update, text)

    async def cmd_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._send(update,
            "What would you like to update? You can tell me:\n\n"
            "• Changes to your weekly availability\n"
            "• New or updated goals\n"
            "• Upcoming events\n"
            "• Any physical issues or illness\n\n"
            "Just describe what's changed in plain language."
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._send(update,
            "🚴 *AI Cycling Coach — Commands*\n\n"
            "/status — CTL, ATL, TSB, FTP trend\n"
            "/plan — This week's prescribed sessions\n"
            "/metrics — Latest FTP and VO2max readings\n"
            "/update — Change availability or goals\n"
            "/help — This menu\n\n"
            "Or just *chat naturally* — ask me anything about your training, "
            "request plan adjustments, or tell me about changes to your schedule."
        )

    # ─── Document upload (dossier) ─────────────────────────────────────────────

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        doc: Document = update.message.document

        allowed_mimes = {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/markdown",
        }
        if doc.mime_type not in allowed_mimes:
            await self._send(update, "Please send a PDF, Word (.docx), or text file.")
            return

        await self._send(update, "📂 Received your document — parsing it now...")

        # Download the file
        file = await context.bot.get_file(doc.file_id)
        ext = Path(doc.file_name).suffix.lower()
        data_dir = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "data")
        save_path = f"{data_dir}/dossier/upload{ext}"
        os.makedirs(f"{data_dir}/dossier", exist_ok=True)
        await file.download_to_drive(save_path)

        try:
            parsed = parse_dossier(save_path)
            self._pending_dossier_confirmation = parsed
            confirmation_text = format_extraction_for_confirmation(parsed)
            await self._send(update, confirmation_text)
        except Exception as e:
            logger.error(f"Dossier parse error: {e}")
            await self._send(update,
                f"⚠️ I had trouble parsing that document: {e}\n\n"
                "You can type *skip* and I'll ask you questions instead."
            )

    # ─── Message handler ───────────────────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        self._save_chat_id(chat_id)

        # ── Dossier confirmation flow ─────────────────────────────────────────
        if self._pending_dossier_confirmation is not None:
            if text.lower() in ("yes", "y", "correct", "looks good", "confirm"):
                parsed = self._pending_dossier_confirmation
                self._apply_parsed_dossier(parsed)
                self._pending_dossier_confirmation = None
                await self._send(update,
                    "✅ Profile saved! Generating your opening training block now..."
                )
                await self._generate_and_send_block(update)
                self.state.complete_onboarding()
                return
            else:
                # Treat non-confirmation as a correction
                await self._send(update,
                    f"Got it — please tell me what's incorrect and I'll fix it. "
                    f"Or send a corrected document."
                )
                return

        # ── Onboarding flow ───────────────────────────────────────────────────
        if not self.state.is_onboarded() and chat_id in _onboarding_sessions:
            if text.lower() == "skip":
                # Start fresh onboarding conversation
                _onboarding_sessions[chat_id] = []
                engine = CoachEngine(self.state)
                reply = engine.onboarding_chat(
                    "Please start by introducing yourself and asking for my first set of details.",
                    []
                )
                _onboarding_sessions[chat_id].append({"role": "assistant", "content": reply})
                await self._send(update, reply)
                return

            history = _onboarding_sessions.get(chat_id, [])
            engine = CoachEngine(self.state)
            reply = engine.onboarding_chat(text, history)

            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply})
            _onboarding_sessions[chat_id] = history

            if "ONBOARDING_COMPLETE" in reply:
                reply = reply.replace("ONBOARDING_COMPLETE", "").strip()
                await self._send(update, reply)
                # Extract profile from conversation
                await self._extract_profile_from_onboarding(history, update, engine)
                del _onboarding_sessions[chat_id]
            else:
                await self._send(update, reply)
            return

        # ── Normal conversational coaching ────────────────────────────────────
        engine = CoachEngine(self.state)

        # Check for availability/goal update intent and apply to state
        availability_changes = engine.parse_availability_update(text)
        if availability_changes:
            self.state.update_availability(availability_changes)
            logger.info(f"Availability updated: {availability_changes}")

        # ── Pull live Strava + Garmin data before every response ──────────────
        # This means the coach always has current data during conversation,
        # not just during the automated morning briefing.
        live_context = await self._fetch_live_data()

        reply = engine.chat(text, garmin_metrics=live_context.get("garmin"),
                            live_strava=live_context.get("strava"))
        await self._send(update, reply)

    async def _fetch_live_data(self) -> dict:
        """
        Pull fresh Strava and Garmin data in the background before responding.
        Returns whatever is available — silently ignores failures so a bad
        API connection never blocks the conversation.
        """
        import asyncio
        result = {}
        ftp = self.state.get_current_ftp() or 200

        # Run both fetches concurrently so it's fast
        async def fetch_strava():
            try:
                from clients.strava_client import StravaClient
                strava = StravaClient()
                summary = await asyncio.to_thread(strava.get_28_day_summary, ftp)
                recent = await asyncio.to_thread(strava.get_recent_activities, 7)
                # Log any new activities
                for a in summary.get("activities", []):
                    self.state.log_activity(a)
                self.state.recalculate_pmc()
                result["strava"] = {
                    "recent_activities": recent[:5],  # last 5 rides
                    "28_day_summary": summary,
                }
                logger.info("[LiveData] Strava fetched")
            except Exception as e:
                logger.warning(f"[LiveData] Strava fetch skipped: {e}")

        async def fetch_garmin():
            try:
                from clients.garmin_client import GarminClient
                garmin = GarminClient()
                metrics = await asyncio.to_thread(garmin.get_todays_metrics)
                if metrics.get("ftp_estimate_w"):
                    self.state.record_ftp(metrics["ftp_estimate_w"], source="garmin")
                if metrics.get("vo2max"):
                    self.state.record_vo2max(metrics["vo2max"], source="garmin")
                result["garmin"] = metrics
                logger.info("[LiveData] Garmin fetched")
            except Exception as e:
                logger.warning(f"[LiveData] Garmin fetch skipped: {e}")

        await asyncio.gather(fetch_strava(), fetch_garmin())
        return result

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _apply_parsed_dossier(self, parsed: dict):
        """Write parsed dossier data into training state."""
        if parsed.get("athlete"):
            self.state.state["athlete"].update(parsed["athlete"])
        if parsed.get("goals"):
            self.state.state["goals"].update(parsed["goals"])
        if parsed.get("availability"):
            self.state.state["availability"].update(parsed["availability"])
        if parsed.get("ftp_history"):
            self.state.state["ftp_history"].extend(parsed["ftp_history"])
        if parsed.get("vo2max_history"):
            self.state.state["vo2max_history"].extend(parsed["vo2max_history"])
        self.state.save()

    async def _extract_profile_from_onboarding(self, history: list, update: Update, engine: CoachEngine):
        """After onboarding completes, ask Claude to extract structured profile from conversation."""
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        conversation_text = "\n".join(
            f"{h['role'].upper()}: {h['content']}" for h in history
        )

        prompt = f"""Extract the athlete profile from this onboarding conversation.
Return ONLY a valid JSON object with this structure (no markdown):
{{
  "athlete": {{"name": null, "ftp_w": null, "weight_kg": null, "vo2max": null,
               "years_cycling": null, "level": null, "strengths": [], "limiters": [], "injury_flags": []}},
  "goals": {{"primary_goal": null, "primary_goal_date": null, "secondary_goals": [],
             "events": [], "weekly_hour_budget": null}},
  "availability": {{"Monday": null, "Tuesday": null, "Wednesday": null,
                    "Thursday": null, "Friday": null, "Saturday": null, "Sunday": null}}
}}

Conversation:
{conversation_text}"""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        try:
            parsed = json.loads(raw)
            self._apply_parsed_dossier(parsed)
        except Exception as e:
            logger.error(f"Profile extraction error: {e}")

        await self._generate_and_send_block(update)
        self.state.complete_onboarding()

    async def _generate_and_send_block(self, update: Update):
        """Generate a 4-week training block and send a summary."""
        try:
            engine = CoachEngine(self.state)
            plan = engine.generate_training_block(weeks=4)

            # Save block to state
            self.state.update_block({
                "type": plan.get("block_type"),
                "total_weeks": plan.get("total_weeks"),
                "week_number": 1,
                "start_date": date.today().isoformat(),
                "target_tss_week": plan.get("target_weekly_tss"),
                "sessions": plan["weeks"][0]["sessions"] if plan.get("weeks") else [],
            })

            summary = (
                f"✅ *Your training block is ready!*\n\n"
                f"*Block type:* {plan.get('block_type', '').title()}\n"
                f"*Duration:* {plan.get('total_weeks')} weeks\n"
                f"*Target weekly TSS:* {plan.get('target_weekly_tss')}\n\n"
                f"_{plan.get('rationale', '')}_\n\n"
                f"Use /plan to see this week's sessions.\n"
                f"I'll send you a morning briefing every day at 07:00 🌅"
            )
            await update.message.reply_text(summary, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Block generation error: {e}")
            await update.message.reply_text(
                "⚠️ I had trouble generating your training block. "
                "Try asking me: 'Generate a 4-week training block for me.'"
            )

    # ─── Public send method (used by scheduler) ───────────────────────────────

    def send_message_sync(self, text: str):
        """
        Send a message to the configured chat (used by the daily briefing scheduler).
        This is a synchronous wrapper — call from outside the async event loop.
        """
        import asyncio
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not chat_id:
            print("[Telegram] No TELEGRAM_CHAT_ID set — cannot send scheduled message")
            return

        async def _send():
            bot = self.app.bot
            max_len = 4000
            for i in range(0, len(text), max_len):
                await bot.send_message(
                    chat_id=int(chat_id),
                    text=text[i:i + max_len],
                    parse_mode="Markdown"
                )

        asyncio.run(_send())

    def run(self):
        """Start the Telegram bot polling loop."""
        print("[Telegram] Bot started — listening for messages...")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)
