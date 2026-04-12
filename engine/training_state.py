"""
training_state.py
Manages all persistent coaching state:
- CTL / ATL / TSB (Performance Management Chart)
- FTP and VO2max history and trends
- Athlete profile, goals, availability
- Current training block
- Conversation history
"""

import json
import os
from datetime import datetime, date, timedelta
from typing import Optional
import numpy as np

import os as _os
# Use Railway persistent volume if available, otherwise local data folder
_DATA_DIR = _os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", _os.path.join(_os.path.dirname(__file__), "..", "data"))
STATE_PATH = _os.path.join(_DATA_DIR, "coaching_state.json")
ACTIVITY_LOG_PATH = _os.path.join(_DATA_DIR, "activity_log.json")


def _load_json(path: str, default) -> dict | list:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


class TrainingState:
    def __init__(self):
        self.state = _load_json(STATE_PATH, self._default_state())
        self.activity_log = _load_json(ACTIVITY_LOG_PATH, [])

    def _default_state(self) -> dict:
        return {
            "athlete": {
                "name": None,
                "ftp_w": None,
                "weight_kg": None,
                "vo2max": None,
                "years_cycling": None,
                "level": None,           # recreational / sportive / competitive
                "strengths": [],
                "limiters": [],
                "injury_flags": [],
            },
            "goals": {
                "primary_goal": None,
                "primary_goal_date": None,
                "secondary_goals": [],
                "events": [],            # [{name, date, priority}]
                "weekly_hour_budget": None,
            },
            "availability": {
                "Monday": None,
                "Tuesday": None,
                "Wednesday": None,
                "Thursday": None,
                "Friday": None,
                "Saturday": None,
                "Sunday": None,
            },
            "current_block": {
                "type": None,            # base / build / peak / recovery
                "week_number": 1,
                "total_weeks": None,
                "start_date": None,
                "target_tss_week": None,
                "sessions": [],          # prescribed sessions for current week
            },
            "ftp_history": [],           # [{date, ftp_w}]
            "vo2max_history": [],        # [{date, vo2max}]
            "pmc": {                     # Performance Management Chart
                "ctl": 0.0,             # Chronic Training Load (fitness)
                "atl": 0.0,             # Acute Training Load (fatigue)
                "tsb": 0.0,             # Training Stress Balance (form)
                "last_updated": None,
            },
            "conversation_history": [],  # [{role, content, timestamp}]
            "onboarding_complete": False,
            "last_briefing_date": None,
        }

    def save(self):
        _save_json(STATE_PATH, self.state)

    def save_activities(self):
        _save_json(ACTIVITY_LOG_PATH, self.activity_log)

    # ─── Athlete Profile ──────────────────────────────────────────────────────

    def update_athlete(self, **kwargs):
        for k, v in kwargs.items():
            if k in self.state["athlete"]:
                self.state["athlete"][k] = v
        self.save()

    def update_goals(self, **kwargs):
        for k, v in kwargs.items():
            if k in self.state["goals"]:
                self.state["goals"][k] = v
        self.save()

    def update_availability(self, availability: dict):
        """availability = {Monday: '90min', Tuesday: 'rest', ...}"""
        self.state["availability"].update(availability)
        self.save()

    def get_current_ftp(self) -> Optional[float]:
        history = self.state.get("ftp_history", [])
        if not history:
            return self.state["athlete"].get("ftp_w")
        return sorted(history, key=lambda x: x["date"])[-1]["ftp_w"]

    def record_ftp(self, ftp_w: float, source: str = "garmin"):
        today = date.today().isoformat()
        self.state["ftp_history"].append({
            "date": today, "ftp_w": ftp_w, "source": source
        })
        self.state["athlete"]["ftp_w"] = ftp_w
        self.save()

    def record_vo2max(self, vo2max: float, source: str = "garmin"):
        today = date.today().isoformat()
        self.state["vo2max_history"].append({
            "date": today, "vo2max": vo2max, "source": source
        })
        self.state["athlete"]["vo2max"] = vo2max
        self.save()

    def get_ftp_trend(self) -> dict:
        """Compare current FTP to 4 weeks ago and 12 weeks ago."""
        history = sorted(self.state["ftp_history"], key=lambda x: x["date"])
        if len(history) < 2:
            return {"current": self.get_current_ftp(), "change_4w": None, "change_12w": None}
        
        current = history[-1]["ftp_w"]
        now = date.today()

        def find_nearest(days_ago):
            target = (now - timedelta(days=days_ago)).isoformat()
            past = [h for h in history if h["date"] <= target]
            return past[-1]["ftp_w"] if past else None

        past_4w = find_nearest(28)
        past_12w = find_nearest(84)
        return {
            "current": current,
            "change_4w": round(current - past_4w, 1) if past_4w else None,
            "change_12w": round(current - past_12w, 1) if past_12w else None,
        }

    # ─── Activity Log ─────────────────────────────────────────────────────────

    def log_activity(self, activity: dict):
        """Add or update an activity in the log."""
        existing_ids = {a["id"] for a in self.activity_log if "id" in a}
        if activity.get("id") not in existing_ids:
            self.activity_log.append(activity)
            self.save_activities()

    def get_activities_last_n_days(self, n: int) -> list[dict]:
        cutoff = (date.today() - timedelta(days=n)).isoformat()
        return [a for a in self.activity_log if a.get("date", "") >= cutoff]

    # ─── PMC: CTL / ATL / TSB ─────────────────────────────────────────────────

    def recalculate_pmc(self, ctl_tc: int = 42, atl_tc: int = 7):
        """
        Recalculate CTL, ATL, TSB from full activity log.
        Uses exponential weighted moving average.
        """
        if not self.activity_log:
            return

        # Build TSS-per-day dict
        tss_by_date = {}
        for a in self.activity_log:
            d = a.get("date")
            tss = a.get("tss", 0) or 0
            if d:
                tss_by_date[d] = tss_by_date.get(d, 0) + tss

        if not tss_by_date:
            return

        # Fill date range with 0s
        all_dates = sorted(tss_by_date.keys())
        start = datetime.strptime(all_dates[0], "%Y-%m-%d").date()
        end = date.today()
        delta = (end - start).days

        ctl = 0.0
        atl = 0.0
        ctl_alpha = 1 - np.exp(-1 / ctl_tc)
        atl_alpha = 1 - np.exp(-1 / atl_tc)

        for i in range(delta + 1):
            d = (start + timedelta(days=i)).isoformat()
            tss = tss_by_date.get(d, 0)
            ctl = ctl + ctl_alpha * (tss - ctl)
            atl = atl + atl_alpha * (tss - atl)

        self.state["pmc"] = {
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(ctl - atl, 1),
            "last_updated": date.today().isoformat(),
        }
        self.save()

    def get_pmc_summary(self) -> dict:
        return self.state["pmc"]

    # ─── Training Block ───────────────────────────────────────────────────────

    def update_block(self, block: dict):
        self.state["current_block"].update(block)
        self.save()

    def get_this_weeks_sessions(self) -> list[dict]:
        return self.state["current_block"].get("sessions", [])

    def mark_session_complete(self, session_id: str):
        for s in self.state["current_block"]["sessions"]:
            if s.get("id") == session_id:
                s["status"] = "complete"
        self.save()

    # ─── Conversation History ─────────────────────────────────────────────────

    def add_conversation_turn(self, role: str, content: str):
        self.state["conversation_history"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep last 40 turns to manage context size
        if len(self.state["conversation_history"]) > 40:
            self.state["conversation_history"] = self.state["conversation_history"][-40:]
        self.save()

    def get_conversation_history(self, last_n: int = 20) -> list[dict]:
        """Return last N turns in {role, content} format for Claude API."""
        turns = self.state["conversation_history"][-last_n:]
        return [{"role": t["role"], "content": t["content"]} for t in turns]

    # ─── Onboarding ───────────────────────────────────────────────────────────

    def is_onboarded(self) -> bool:
        return self.state.get("onboarding_complete", False)

    def complete_onboarding(self):
        self.state["onboarding_complete"] = True
        self.save()

    def get_full_profile_summary(self) -> str:
        """
        Returns a formatted string summary of the athlete profile.
        Used as part of the Claude system prompt.
        """
        s = self.state
        a = s["athlete"]
        g = s["goals"]
        av = s["availability"]
        pmc = s["pmc"]
        ftp_trend = self.get_ftp_trend()

        lines = [
            "=== ATHLETE PROFILE ===",
            f"Name: {a.get('name', 'Unknown')}",
            f"FTP: {a.get('ftp_w')}W | VO2max: {a.get('vo2max')} ml/kg/min",
            f"Weight: {a.get('weight_kg')}kg | W/kg: {round(a['ftp_w']/a['weight_kg'], 2) if a.get('ftp_w') and a.get('weight_kg') else 'N/A'}",
            f"Level: {a.get('level')} | Years cycling: {a.get('years_cycling')}",
            f"Strengths: {', '.join(a.get('strengths', []))}",
            f"Limiters: {', '.join(a.get('limiters', []))}",
            f"Injury flags: {', '.join(a.get('injury_flags', [])) or 'None'}",
            "",
            "=== GOALS ===",
            f"Primary: {g.get('primary_goal')} (by {g.get('primary_goal_date')})",
            f"Weekly hour budget: {g.get('weekly_hour_budget')}h",
        ]
        if g.get("events"):
            lines.append("Events:")
            for e in g["events"]:
                lines.append(f"  - {e['name']}: {e['date']}")

        lines += [
            "",
            "=== WEEKLY AVAILABILITY ===",
        ]
        for day, avail in av.items():
            lines.append(f"  {day}: {avail or 'Not set'}")

        lines += [
            "",
            "=== FITNESS STATUS ===",
            f"CTL (fitness): {pmc.get('ctl')}",
            f"ATL (fatigue): {pmc.get('atl')}",
            f"TSB (form):    {pmc.get('tsb')}",
            f"FTP trend: current {ftp_trend['current']}W | 4w change: {ftp_trend['change_4w']}W | 12w change: {ftp_trend['change_12w']}W",
            "",
            "=== CURRENT TRAINING BLOCK ===",
            f"Type: {s['current_block'].get('type')} | Week {s['current_block'].get('week_number')} of {s['current_block'].get('total_weeks')}",
            f"Target weekly TSS: {s['current_block'].get('target_tss_week')}",
        ]
        return "\n".join(lines)
