"""
strava_client.py
Handles Strava OAuth2 authentication and activity data fetching.
"""

import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

STRAVA_API_BASE = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"


class StravaClient:
    def __init__(self):
        self.client_id = os.getenv("STRAVA_CLIENT_ID")
        self.client_secret = os.getenv("STRAVA_CLIENT_SECRET")
        self.refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")
        self.access_token = None
        self.token_expiry = 0

    def _refresh_access_token(self):
        """Exchange refresh token for a fresh access token."""
        response = requests.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        })
        response.raise_for_status()
        data = response.json()
        self.access_token = data["access_token"]
        self.token_expiry = data["expires_at"]

        # Persist updated refresh token if it rotated
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self.refresh_token:
            self.refresh_token = new_refresh
            self._update_env_token(new_refresh)

    def _update_env_token(self, token: str):
        """Write updated refresh token back to .env file."""
        env_path = ".env"
        if not os.path.exists(env_path):
            return
        with open(env_path, "r") as f:
            lines = f.readlines()
        with open(env_path, "w") as f:
            for line in lines:
                if line.startswith("STRAVA_REFRESH_TOKEN="):
                    f.write(f"STRAVA_REFRESH_TOKEN={token}\n")
                else:
                    f.write(line)

    def _get_headers(self) -> dict:
        """Return auth headers, refreshing token if expired."""
        if not self.access_token or datetime.now().timestamp() >= self.token_expiry - 60:
            self._refresh_access_token()
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_athlete(self) -> dict:
        """Fetch basic athlete profile."""
        r = requests.get(f"{STRAVA_API_BASE}/athlete", headers=self._get_headers())
        r.raise_for_status()
        return r.json()

    def get_recent_activities(self, days: int = 28) -> list[dict]:
        """
        Fetch activities from the last N days.
        Returns list of activity summaries.
        """
        after = int((datetime.now() - timedelta(days=days)).timestamp())
        activities = []
        page = 1

        while True:
            r = requests.get(
                f"{STRAVA_API_BASE}/athlete/activities",
                headers=self._get_headers(),
                params={"after": after, "per_page": 50, "page": page}
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            activities.extend(batch)
            page += 1

        return [self._parse_activity(a) for a in activities if a.get("type") in ("Ride", "VirtualRide")]

    def get_yesterdays_activity(self) -> dict | None:
        """Fetch the most recent cycling activity from yesterday."""
        yesterday = datetime.now() - timedelta(days=1)
        after = int(datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0).timestamp())
        before = int(datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59).timestamp())

        r = requests.get(
            f"{STRAVA_API_BASE}/athlete/activities",
            headers=self._get_headers(),
            params={"after": after, "before": before, "per_page": 10}
        )
        r.raise_for_status()
        activities = [a for a in r.json() if a.get("type") in ("Ride", "VirtualRide")]
        if not activities:
            return None
        return self._parse_activity(activities[0])

    def get_activity_streams(self, activity_id: int) -> dict:
        """
        Fetch detailed power/HR streams for a specific activity.
        Returns watts and heart rate time series.
        """
        r = requests.get(
            f"{STRAVA_API_BASE}/activities/{activity_id}/streams",
            headers=self._get_headers(),
            params={"keys": "watts,heartrate,time,velocity_smooth", "key_by_type": "true"}
        )
        r.raise_for_status()
        return r.json()

    def _parse_activity(self, a: dict) -> dict:
        """Extract the key metrics from a raw Strava activity."""
        return {
            "id": a.get("id"),
            "name": a.get("name"),
            "date": a.get("start_date_local", "")[:10],
            "type": a.get("type"),
            "duration_sec": a.get("moving_time", 0),
            "duration_min": round(a.get("moving_time", 0) / 60, 1),
            "distance_km": round((a.get("distance", 0) / 1000), 1),
            "elevation_m": round(a.get("total_elevation_gain", 0), 0),
            "avg_power_w": a.get("average_watts"),
            "weighted_avg_power_w": a.get("weighted_average_watts"),  # Normalised Power
            "max_power_w": a.get("max_watts"),
            "avg_hr_bpm": a.get("average_heartrate"),
            "max_hr_bpm": a.get("max_heartrate"),
            "avg_cadence_rpm": a.get("average_cadence"),
            "kilojoules": a.get("kilojoules"),
            "suffer_score": a.get("suffer_score"),
            "trainer": a.get("trainer", False),  # True = indoor/Zwift
            "kudos": a.get("kudos_count", 0),
        }

    def calculate_tss(self, activity: dict, ftp: float) -> float | None:
        """
        Calculate Training Stress Score from activity data.
        TSS = (duration_sec * NP * IF) / (FTP * 3600) * 100
        """
        np = activity.get("weighted_avg_power_w")
        duration = activity.get("duration_sec")
        if not np or not duration or not ftp:
            return None
        intensity_factor = np / ftp
        tss = (duration * np * intensity_factor) / (ftp * 3600) * 100
        return round(tss, 1)

    def get_28_day_summary(self, ftp: float) -> dict:
        """
        Build a 28-day training summary with TSS per activity.
        Used as rolling context for the coach.
        """
        activities = self.get_recent_activities(days=28)
        for a in activities:
            a["tss"] = self.calculate_tss(a, ftp)
        
        total_tss = sum(a["tss"] for a in activities if a["tss"])
        total_hours = sum(a["duration_min"] for a in activities) / 60

        return {
            "activities": activities,
            "total_activities": len(activities),
            "total_hours": round(total_hours, 1),
            "total_tss": round(total_tss, 1),
            "avg_weekly_tss": round(total_tss / 4, 1),
        }
