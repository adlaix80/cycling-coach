"""
garmin_client.py
Fetches physiological metrics from Garmin Connect:
VO2max, FTP estimate, HRV status, sleep score, training readiness.
Uses the garminconnect community library.
"""

import os
from datetime import date, timedelta
from garminconnect import Garmin
from dotenv import load_dotenv

load_dotenv()


class GarminClient:
    def __init__(self):
        self.email = os.getenv("GARMIN_EMAIL")
        self.password = os.getenv("GARMIN_PASSWORD")
        self._client = None

    def _connect(self):
        """Authenticate and return a connected Garmin client."""
        if self._client is None:
            client = Garmin(self.email, self.password)
            client.login()
            self._client = client
        return self._client

    def get_todays_metrics(self) -> dict:
        """
        Pull today's key physiological metrics from Garmin Connect.
        Returns a structured dict for use by the coach engine.
        """
        client = self._connect()
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        metrics = {
            "date": today,
            "vo2max": None,
            "ftp_estimate_w": None,
            "hrv_status": None,
            "hrv_last_night_ms": None,
            "sleep_score": None,
            "sleep_duration_hr": None,
            "resting_hr_bpm": None,
            "training_readiness_score": None,
            "training_readiness_level": None,
            "body_battery": None,
        }

        # --- VO2max ---
        try:
            vo2 = client.get_max_metrics(today)
            if vo2 and isinstance(vo2, list) and len(vo2) > 0:
                metrics["vo2max"] = vo2[0].get("generic", {}).get("vo2MaxValue")
        except Exception as e:
            print(f"[Garmin] VO2max fetch failed: {e}")

        # --- FTP (Garmin cycling FTP estimate) ---
        try:
            perf = client.get_performance_stats(today)
            if perf:
                metrics["ftp_estimate_w"] = perf.get("ftpValue")
        except Exception as e:
            print(f"[Garmin] FTP fetch failed: {e}")

        # --- HRV ---
        try:
            hrv = client.get_hrv_data(today)
            if hrv:
                summary = hrv.get("hrvSummary", {})
                metrics["hrv_status"] = summary.get("status")          # e.g. "BALANCED", "UNBALANCED"
                metrics["hrv_last_night_ms"] = summary.get("lastNight")
        except Exception as e:
            print(f"[Garmin] HRV fetch failed: {e}")

        # --- Sleep ---
        try:
            sleep = client.get_sleep_data(yesterday)  # last night = yesterday's date
            if sleep:
                daily = sleep.get("dailySleepDTO", {})
                metrics["sleep_score"] = daily.get("sleepScores", {}).get("overall", {}).get("value")
                sleep_sec = daily.get("sleepTimeSeconds", 0)
                metrics["sleep_duration_hr"] = round(sleep_sec / 3600, 1) if sleep_sec else None
        except Exception as e:
            print(f"[Garmin] Sleep fetch failed: {e}")

        # --- Resting HR ---
        try:
            rhr = client.get_rhr_day(today)
            if rhr:
                metrics["resting_hr_bpm"] = rhr.get("allDayHR", {}).get("restingHeartRate")
        except Exception as e:
            print(f"[Garmin] Resting HR fetch failed: {e}")

        # --- Training Readiness ---
        try:
            readiness = client.get_training_readiness(today)
            if readiness and isinstance(readiness, list) and len(readiness) > 0:
                r = readiness[0]
                metrics["training_readiness_score"] = r.get("score")
                metrics["training_readiness_level"] = r.get("level")  # e.g. "LOW", "MODERATE", "HIGH"
        except Exception as e:
            print(f"[Garmin] Training readiness fetch failed: {e}")

        # --- Body Battery ---
        try:
            bb = client.get_body_battery(today)
            if bb and isinstance(bb, list) and len(bb) > 0:
                readings = bb[0].get("bodyBatteryValuesArray", [])
                if readings:
                    metrics["body_battery"] = readings[-1][1]  # latest value
        except Exception as e:
            print(f"[Garmin] Body battery fetch failed: {e}")

        return metrics

    def get_vo2max_history(self, days: int = 90) -> list[dict]:
        """
        Fetch VO2max history over the last N days.
        Returns list of {date, vo2max} for trend analysis.
        """
        client = self._connect()
        history = []
        for i in range(days, 0, -7):  # weekly samples
            d = (date.today() - timedelta(days=i)).isoformat()
            try:
                vo2 = client.get_max_metrics(d)
                if vo2 and isinstance(vo2, list) and len(vo2) > 0:
                    val = vo2[0].get("generic", {}).get("vo2MaxValue")
                    if val:
                        history.append({"date": d, "vo2max": val})
            except Exception:
                continue
        return history

    def get_ftp_history(self, days: int = 90) -> list[dict]:
        """
        Fetch Garmin FTP estimate history over the last N days.
        Returns list of {date, ftp_w} for trend analysis.
        """
        client = self._connect()
        history = []
        for i in range(days, 0, -7):
            d = (date.today() - timedelta(days=i)).isoformat()
            try:
                perf = client.get_performance_stats(d)
                if perf and perf.get("ftpValue"):
                    history.append({"date": d, "ftp_w": perf["ftpValue"]})
            except Exception:
                continue
        return history
