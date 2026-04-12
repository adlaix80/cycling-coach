"""
plan_builder.py
Manages the lifecycle of training plans:
- Advancing through weeks within a block
- Detecting when a block is complete and triggering a new one
- Inserting recovery weeks automatically (3:1 pattern)
- Matching planned vs completed sessions
- Adjusting upcoming sessions based on fatigue or missed workouts
"""

import os
import json
import logging
from datetime import date, timedelta
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"


class PlanBuilder:
    def __init__(self, training_state):
        self.state = training_state
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # ─── Week Management ───────────────────────────────────────────────────────

    def advance_week(self) -> dict:
        """
        Called every Monday morning by the scheduler.
        Advances the block by one week, handles 3:1 periodisation,
        generates sessions for the new week.
        Returns the new week's sessions.
        """
        block = self.state.state["current_block"]
        current_week = block.get("week_number", 1)
        total_weeks = block.get("total_weeks", 4)

        # Check if block is complete
        if current_week >= total_weeks:
            logger.info("[PlanBuilder] Block complete — generating new block")
            return self._start_new_block()

        # Advance week counter
        new_week = current_week + 1
        block["week_number"] = new_week

        # Determine if this should be a recovery week (every 4th week)
        is_recovery_week = (new_week % 4 == 0)

        # Generate sessions for the new week
        sessions = self._generate_week_sessions(
            week_number=new_week,
            block_type=block.get("type", "base"),
            is_recovery=is_recovery_week,
            target_tss=block.get("target_tss_week", 300),
        )

        block["sessions"] = sessions
        self.state.save()

        logger.info(f"[PlanBuilder] Advanced to week {new_week} "
                    f"({'recovery' if is_recovery_week else block.get('type')})")
        return sessions

    def _start_new_block(self) -> dict:
        """
        Determine and generate the next training block based on:
        - Current fitness (CTL/ATL)
        - FTP trend
        - Time to next A-event
        - Previous block type (avoid repeating)
        """
        from engine.coach_engine import CoachEngine
        engine = CoachEngine(self.state)

        # Let Claude decide the next block type and generate the plan
        plan = engine.generate_training_block(weeks=4)

        next_block = {
            "type": plan.get("block_type"),
            "total_weeks": plan.get("total_weeks", 4),
            "week_number": 1,
            "start_date": date.today().isoformat(),
            "target_tss_week": plan.get("target_weekly_tss"),
            "sessions": plan["weeks"][0]["sessions"] if plan.get("weeks") else [],
            "full_plan": plan,  # store complete multi-week plan
        }
        self.state.update_block(next_block)
        return next_block["sessions"]

    def _generate_week_sessions(self, week_number: int, block_type: str,
                                  is_recovery: bool, target_tss: float) -> list[dict]:
        """
        Generate sessions for a specific week using Claude.
        Respects athlete availability and adjusts for recovery weeks.
        """
        availability = self.state.state.get("availability", {})
        ftp = self.state.get_current_ftp() or 200
        pmc = self.state.get_pmc_summary()

        available_days = {
            day: avail for day, avail in availability.items()
            if avail and avail.lower() != "rest"
        }

        effective_tss = target_tss * 0.6 if is_recovery else target_tss
        week_type = "recovery" if is_recovery else block_type

        prompt = f"""Generate cycling training sessions for week {week_number} of a {block_type} block.
{"This is a RECOVERY week — reduce intensity and volume significantly." if is_recovery else ""}

Athlete data:
- FTP: {ftp}W
- CTL: {pmc.get('ctl')}, ATL: {pmc.get('atl')}, TSB: {pmc.get('tsb')}
- Target weekly TSS: {effective_tss}
- Available days and time limits: {json.dumps(available_days)}

Return ONLY a valid JSON array of session objects (no markdown):
[
  {{
    "id": "w{week_number}_mon",
    "day": "Monday",
    "type": "recovery|endurance|tempo|sweet_spot|threshold|vo2max|rest",
    "title": "session name",
    "duration_min": number,
    "tss_estimate": number,
    "description": "full description with specific watt targets",
    "warm_up": "warm-up protocol",
    "main_set": "main set with intervals if applicable",
    "cool_down": "cool-down protocol",
    "status": "planned"
  }}
]

Include only days from the available days list. Respect time limits per day."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        return json.loads(raw.strip())

    # ─── Session Matching & Compliance ────────────────────────────────────────

    def match_completed_to_planned(self, completed_activity: dict) -> Optional[dict]:
        """
        Try to match a completed Strava activity to a planned session.
        Returns the matched session dict, or None if no match found.
        Matching logic: same day + similar duration (±30%) or type keyword.
        """
        sessions = self.state.get_this_weeks_sessions()
        activity_date = completed_activity.get("date")
        if not activity_date:
            return None

        activity_day = date.fromisoformat(activity_date).strftime("%A")
        duration_min = completed_activity.get("duration_min", 0)

        for session in sessions:
            if session.get("day") == activity_day and session.get("status") != "complete":
                planned_duration = session.get("duration_min", 0)
                # Match if duration within 30% of planned
                if planned_duration > 0:
                    ratio = duration_min / planned_duration
                    if 0.5 <= ratio <= 1.5:
                        return session
        return None

    def mark_session_complete(self, session_id: str, actual_tss: float = None):
        """Mark a planned session as completed, optionally recording actual TSS."""
        for s in self.state.state["current_block"]["sessions"]:
            if s.get("id") == session_id:
                s["status"] = "complete"
                if actual_tss:
                    s["actual_tss"] = actual_tss
        self.state.save()

    def get_compliance_report(self) -> dict:
        """
        Calculate session compliance for the current week.
        Returns planned vs completed counts and TSS.
        """
        sessions = self.state.get_this_weeks_sessions()
        planned = [s for s in sessions if s.get("type") != "rest"]
        completed = [s for s in planned if s.get("status") == "complete"]

        planned_tss = sum(s.get("tss_estimate", 0) for s in planned)
        actual_tss = sum(
            s.get("actual_tss", s.get("tss_estimate", 0))
            for s in completed
        )

        return {
            "sessions_planned": len(planned),
            "sessions_completed": len(completed),
            "compliance_pct": round(len(completed) / len(planned) * 100) if planned else 0,
            "planned_tss": planned_tss,
            "actual_tss": actual_tss,
            "tss_compliance_pct": round(actual_tss / planned_tss * 100) if planned_tss else 0,
        }

    # ─── Dynamic Adjustments ──────────────────────────────────────────────────

    def adjust_remaining_week(self, reason: str) -> list[dict]:
        """
        Dynamically adjust remaining sessions in the week.
        Called when: athlete reports illness, excessive fatigue, availability change.
        reason: plain-language description of why adjustment is needed.
        """
        sessions = self.state.get_this_weeks_sessions()
        today = date.today().strftime("%A")
        days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        today_idx = days_order.index(today) if today in days_order else 0

        remaining = [
            s for s in sessions
            if days_order.index(s["day"]) >= today_idx and s.get("status") != "complete"
        ]

        if not remaining:
            return []

        ftp = self.state.get_current_ftp() or 200
        pmc = self.state.get_pmc_summary()

        prompt = f"""A cyclist needs to adjust their remaining week's training.

Reason: {reason}

Current fitness: CTL={pmc.get('ctl')}, ATL={pmc.get('atl')}, TSB={pmc.get('tsb')}
FTP: {ftp}W

Remaining planned sessions (to be adjusted):
{json.dumps(remaining, indent=2)}

Return ONLY a valid JSON array of the adjusted sessions, maintaining the same structure
but modifying type, duration, intensity, and description as appropriate.
Keep the same day assignments unless the reason requires moving sessions."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        adjusted = json.loads(raw.strip())

        # Merge adjusted sessions back into the full week
        completed = [s for s in sessions if s.get("status") == "complete"]
        self.state.state["current_block"]["sessions"] = completed + adjusted
        self.state.save()

        return adjusted

    def insert_peak_week(self, event_date_str: str) -> list[dict]:
        """
        Insert a taper/peak week before a target event.
        Called when an A-event is within 2 weeks.
        """
        event_date = date.fromisoformat(event_date_str)
        days_out = (event_date - date.today()).days
        ftp = self.state.get_current_ftp() or 200

        taper_type = "race_taper" if days_out <= 7 else "pre_event_sharpener"

        prompt = f"""Generate a {taper_type} week for a cyclist with:
- FTP: {ftp}W
- Days until event: {days_out}
- Event date: {event_date_str}
- Availability: {json.dumps(self.state.state.get('availability', {}))}

The week should reduce volume by 40-50% while maintaining intensity.
Include one quality session early in the week, then progressively freshen up.

Return ONLY a valid JSON array of session objects with the standard structure."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        taper_sessions = json.loads(raw.strip())
        self.state.state["current_block"]["sessions"] = taper_sessions
        self.state.state["current_block"]["type"] = taper_type
        self.state.save()

        return taper_sessions

    # ─── Event Proximity Check ────────────────────────────────────────────────

    def check_upcoming_events(self) -> Optional[dict]:
        """
        Check if any A-priority event is within 14 days.
        Returns the event dict if found, else None.
        """
        events = self.state.state.get("goals", {}).get("events", [])
        today = date.today()
        for event in events:
            if event.get("priority") == "A":
                try:
                    event_date = date.fromisoformat(event["date"])
                    days_out = (event_date - today).days
                    if 0 < days_out <= 14:
                        return {**event, "days_out": days_out}
                except (ValueError, KeyError):
                    continue
        return None
