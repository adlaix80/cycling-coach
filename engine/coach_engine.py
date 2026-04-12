"""
coach_engine.py
The core coaching intelligence layer.
Builds context-rich prompts and calls the Claude API for:
  - Daily workout briefings
  - Conversational responses
  - Training plan generation
"""

import os
import anthropic
from datetime import date
from dotenv import load_dotenv
from engine.training_state import TrainingState

load_dotenv()

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2000


COACH_SYSTEM_PROMPT = """You are an expert cycling coach with deep knowledge of:
- Exercise physiology and training theory (periodisation, CTL/ATL/TSB, power zones)
- Strava and Garmin metrics interpretation
- Indoor training (Zwift) and outdoor training programming
- FTP and VO2max development
- Tapering and peaking for events

Your coaching style is:
- Evidence-based but practical
- Encouraging without being sycophantic  
- Direct and specific — always give precise targets (watts, duration, zones)
- Attentive to athlete wellbeing — HRV, sleep, and readiness inform your decisions
- Adaptive — you update the plan when life changes (availability, goals, illness)

When prescribing sessions, always specify:
- Session type and goal
- Duration
- Power targets in both watts (absolute) and % FTP
- Heart rate zone if relevant
- Specific interval structure if applicable
- Warm-up and cool-down guidance

When analysing completed workouts, comment on:
- Execution quality (did power/HR match targets?)
- Physiological response (IF, TSS, estimated adaptations)
- Any flags (excessive fatigue, underperformance, illness signs)

Format responses clearly. Use markdown for structure where helpful.
Keep daily briefings concise — the athlete reads them on their phone in the morning.
For planning discussions, be thorough.

You always have access to the athlete's full profile, current block, and recent conversation history in the user message context. Use it."""


class CoachEngine:
    def __init__(self, training_state: TrainingState):
        self.state = training_state
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def _build_context_block(self, garmin_metrics: dict = None, yesterday_activity: dict = None,
                              recent_summary: dict = None) -> str:
        """Assembles the full context payload appended to every user message."""
        lines = [
            "--- COACHING CONTEXT ---",
            self.state.get_full_profile_summary(),
        ]

        if yesterday_activity:
            lines += [
                "",
                "=== YESTERDAY'S ACTIVITY ===",
                f"Name: {yesterday_activity.get('name')}",
                f"Type: {yesterday_activity.get('type')} | Date: {yesterday_activity.get('date')}",
                f"Duration: {yesterday_activity.get('duration_min')} min",
                f"Distance: {yesterday_activity.get('distance_km')} km",
                f"Avg Power: {yesterday_activity.get('avg_power_w')}W | NP: {yesterday_activity.get('weighted_avg_power_w')}W",
                f"TSS: {yesterday_activity.get('tss')} | IF: {yesterday_activity.get('intensity_factor')}",
                f"Avg HR: {yesterday_activity.get('avg_hr_bpm')} bpm | Max HR: {yesterday_activity.get('max_hr_bpm')} bpm",
                f"Elevation: {yesterday_activity.get('elevation_m')}m | kJ: {yesterday_activity.get('kilojoules')}",
                f"Indoor (Zwift): {yesterday_activity.get('trainer')}",
            ]
        else:
            lines += ["", "=== YESTERDAY'S ACTIVITY ===", "No cycling activity recorded yesterday."]

        if garmin_metrics:
            lines += [
                "",
                "=== TODAY'S GARMIN METRICS ===",
                f"Training Readiness: {garmin_metrics.get('training_readiness_score')}/100 ({garmin_metrics.get('training_readiness_level')})",
                f"HRV Status: {garmin_metrics.get('hrv_status')} | HRV last night: {garmin_metrics.get('hrv_last_night_ms')} ms",
                f"Sleep score: {garmin_metrics.get('sleep_score')}/100 | Duration: {garmin_metrics.get('sleep_duration_hr')}h",
                f"Resting HR: {garmin_metrics.get('resting_hr_bpm')} bpm",
                f"Body Battery: {garmin_metrics.get('body_battery')}",
                f"VO2max (latest): {garmin_metrics.get('vo2max')} ml/kg/min",
                f"FTP estimate: {garmin_metrics.get('ftp_estimate_w')}W",
            ]

        if recent_summary:
            lines += [
                "",
                "=== 28-DAY TRAINING SUMMARY ===",
                f"Total activities: {recent_summary.get('total_activities')}",
                f"Total hours: {recent_summary.get('total_hours')}h",
                f"Total TSS: {recent_summary.get('total_tss')}",
                f"Avg weekly TSS: {recent_summary.get('avg_weekly_tss')}",
            ]

        lines += [
            "",
            f"Today's date: {date.today().isoformat()}",
            "--- END CONTEXT ---",
        ]
        return "\n".join(lines)

    def generate_daily_briefing(self, yesterday_activity: dict = None,
                                 garmin_metrics: dict = None,
                                 recent_summary: dict = None) -> str:
        """
        Generate the morning automated briefing.
        Analyses yesterday's ride and prescribes today's session.
        """
        context = self._build_context_block(garmin_metrics, yesterday_activity, recent_summary)

        user_message = f"""{context}

Please provide this morning's coaching briefing. Structure it as:

1. **Yesterday's Analysis** — what I did and how I executed it
2. **Readiness Check** — based on HRV, sleep, body battery, TSB
3. **Today's Prescribed Session** — specific targets (or rest/recovery if appropriate)
4. **Weekly Outlook** — brief note on where we are in the training block

Keep it concise — I'm reading this on my phone before training."""

        # Don't add briefing to conversation history (it's automated, not conversational)
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=COACH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}]
        )
        return response.content[0].text

    def chat(self, user_message: str, garmin_metrics: dict = None) -> str:
        """
        Handle a conversational message from the athlete.
        Maintains rolling conversation history.
        """
        context = self._build_context_block(garmin_metrics=garmin_metrics)
        
        # Build messages array with history + context injection
        history = self.state.get_conversation_history(last_n=20)
        
        # Inject context into the first message of the conversation (or current if history empty)
        full_user_message = f"{context}\n\n{user_message}"

        messages = history + [{"role": "user", "content": full_user_message}]

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=COACH_SYSTEM_PROMPT,
            messages=messages
        )

        reply = response.content[0].text

        # Persist to conversation history
        self.state.add_conversation_turn("user", user_message)
        self.state.add_conversation_turn("assistant", reply)

        return reply

    def generate_training_block(self, weeks: int = 4) -> dict:
        """
        Ask Claude to generate a full training block plan.
        Returns structured plan as dict, also saved to state.
        """
        context = self._build_context_block()

        prompt = f"""{context}

Based on the athlete's current fitness, goals, and availability, please generate a {weeks}-week training block.

Respond ONLY with a valid JSON object (no markdown fences) with this structure:
{{
  "block_type": "base|build|peak|recovery",
  "total_weeks": {weeks},
  "rationale": "brief explanation of why this block type",
  "target_weekly_tss": number,
  "weeks": [
    {{
      "week_number": 1,
      "theme": "e.g. Aerobic base development",
      "target_tss": number,
      "sessions": [
        {{
          "id": "w1_mon",
          "day": "Monday",
          "type": "recovery|endurance|tempo|sweet_spot|threshold|vo2max|rest",
          "title": "session name",
          "duration_min": number,
          "tss_estimate": number,
          "description": "full session description with specific power targets",
          "intervals": "optional interval structure",
          "status": "planned"
        }}
      ]
    }}
  ]
}}"""

        import json
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=COACH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        plan = json.loads(raw.strip())
        return plan

    def onboarding_chat(self, user_message: str, onboarding_history: list[dict]) -> str:
        """
        Handles the onboarding conversation flow.
        Uses a separate prompt focused on gathering athlete information.
        """
        onboarding_system = """You are setting up a new athlete profile for an AI cycling coach.
Your job is to gather the following information through natural conversation:
1. Current FTP (watts) and weight (kg)
2. VO2max if known
3. Years cycling and current level (recreational/sportive/competitive)
4. Primary goal and target date
5. Key events on the calendar (name and date)
6. Weekly training availability (each day: rest, or how many minutes available)
7. Strengths and limiters as a cyclist
8. Any injury history or physical flags the coach should know about
9. Weekly hour budget for training

Ask about one or two topics at a time. Be conversational and encouraging.
When you have all the information, summarise it and ask for confirmation.
Once confirmed, say exactly: ONBOARDING_COMPLETE"""

        messages = onboarding_history + [{"role": "user", "content": user_message}]

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=onboarding_system,
            messages=messages
        )
        return response.content[0].text

    def parse_availability_update(self, user_message: str) -> dict:
        """
        Extract availability changes from a natural language message.
        e.g. "Can't ride Tuesday this week, move it to Thursday"
        Returns structured dict for state update.
        """
        import json
        prompt = f"""Extract any schedule or availability changes from this message.
Return ONLY a JSON object with days as keys and availability as values (e.g. "90min", "rest", "60min max").
Only include days that are explicitly changed. If no change is mentioned, return {{}}.

Message: "{user_message}"

Days: Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday"""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        try:
            return json.loads(raw)
        except Exception:
            return {}
