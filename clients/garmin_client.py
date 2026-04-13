"""
garmin_client.py
Garmin data is no longer pulled automatically from the cloud —
Garmin rate-limits logins from cloud server IPs.

Instead, Garmin metrics are entered manually by the athlete via Telegram
and stored in the coaching state. This file provides the data structure
and helper to parse manual inputs.

To log metrics, the athlete types in Telegram e.g.:
  "Log garmin: HRV 45 balanced, sleep 7.5h score 78, readiness 72, resting HR 48"

The coach parses and stores this automatically.
"""

from datetime import date


def empty_metrics() -> dict:
    """Return a blank Garmin metrics dict — used as fallback when no data available."""
    return {
        "date": date.today().isoformat(),
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
        "source": "manual",
    }


def parse_manual_garmin_input(text: str, client) -> dict:
    """
    Use Claude to extract Garmin metrics from a natural language message.
    e.g. "HRV 45 balanced, sleep 7.5h score 78, readiness 72, resting HR 48"
    Returns a metrics dict.
    """
    import json
    import os
    import anthropic
    from dotenv import load_dotenv
    load_dotenv()

    ai = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = f"""Extract Garmin metrics from this message. Return ONLY valid JSON, no markdown.

Message: "{text}"

JSON structure:
{{
  "vo2max": number or null,
  "ftp_estimate_w": number or null,
  "hrv_status": "BALANCED"|"UNBALANCED"|"POOR" or null,
  "hrv_last_night_ms": number or null,
  "sleep_score": number or null,
  "sleep_duration_hr": number or null,
  "resting_hr_bpm": number or null,
  "training_readiness_score": number or null,
  "training_readiness_level": "LOW"|"MODERATE"|"HIGH" or null,
  "body_battery": number or null
}}"""

    response = ai.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    try:
        parsed = json.loads(raw)
        parsed["date"] = date.today().isoformat()
        parsed["source"] = "manual"
        return parsed
    except Exception:
        return empty_metrics()
