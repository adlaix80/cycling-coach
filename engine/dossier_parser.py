"""
dossier_parser.py
Parses an uploaded training dossier (PDF, Word, or text) using Claude
to extract structured athlete profile data.
"""

import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}

EXTRACTION_PROMPT = """You are parsing a cycling training dossier to extract structured athlete information.

Read the document carefully and extract the following fields. If a field is not mentioned, return null.
Respond ONLY with a valid JSON object — no preamble, no markdown fences.

Required JSON structure:
{
  "athlete": {
    "name": string or null,
    "ftp_w": number or null,
    "weight_kg": number or null,
    "vo2max": number or null,
    "years_cycling": number or null,
    "level": "recreational" | "sportive" | "competitive" | null,
    "strengths": [list of strings],
    "limiters": [list of strings],
    "injury_flags": [list of strings]
  },
  "goals": {
    "primary_goal": string or null,
    "primary_goal_date": "YYYY-MM-DD" or null,
    "secondary_goals": [list of strings],
    "events": [{"name": string, "date": "YYYY-MM-DD", "priority": "A"|"B"|"C"}],
    "weekly_hour_budget": number or null
  },
  "availability": {
    "Monday": string or null,
    "Tuesday": string or null,
    "Wednesday": string or null,
    "Thursday": string or null,
    "Friday": string or null,
    "Saturday": string or null,
    "Sunday": string or null
  },
  "ftp_history": [{"date": "YYYY-MM-DD", "ftp_w": number}],
  "vo2max_history": [{"date": "YYYY-MM-DD", "vo2max": number}],
  "notes": string or null
}
"""


def _read_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _read_docx(path: str) -> str:
    try:
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        raise RuntimeError("python-docx not installed. Run: pip install python-docx")


def _read_pdf_as_base64(path: str) -> str:
    """Return base64-encoded PDF for Claude's document API."""
    import base64
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def parse_dossier(file_path: str) -> dict:
    """
    Parse a training dossier file and return structured athlete data.
    Supports PDF, DOCX, TXT, MD.
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    ext = os.path.splitext(file_path)[1].lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {SUPPORTED_EXTENSIONS}")

    print(f"[Dossier] Parsing {file_path} ({ext})...")

    # Build the message content based on file type
    if ext == ".pdf":
        pdf_b64 = _read_pdf_as_base64(file_path)
        message_content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64,
                }
            },
            {
                "type": "text",
                "text": EXTRACTION_PROMPT
            }
        ]
    else:
        # Text-based formats
        if ext == ".docx":
            text = _read_docx(file_path)
        else:
            text = _read_txt(file_path)

        message_content = [
            {
                "type": "text",
                "text": f"{EXTRACTION_PROMPT}\n\n--- DOCUMENT START ---\n{text}\n--- DOCUMENT END ---"
            }
        ]

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": message_content}]
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
        print("[Dossier] Extraction successful.")
        return parsed
    except json.JSONDecodeError as e:
        print(f"[Dossier] JSON parse error: {e}\nRaw response:\n{raw}")
        raise


def format_extraction_for_confirmation(parsed: dict) -> str:
    """
    Format the parsed dossier data into a human-readable summary
    for the athlete to confirm via Telegram.
    """
    a = parsed.get("athlete", {})
    g = parsed.get("goals", {})
    av = parsed.get("availability", {})

    lines = [
        "📋 *Here's what I've extracted from your training dossier:*\n",
        f"👤 *Athlete*",
        f"  Name: {a.get('name', '—')}",
        f"  FTP: {a.get('ftp_w', '—')}W",
        f"  Weight: {a.get('weight_kg', '—')}kg",
        f"  VO2max: {a.get('vo2max', '—')} ml/kg/min",
        f"  Level: {a.get('level', '—')}",
        f"  Strengths: {', '.join(a.get('strengths', [])) or '—'}",
        f"  Limiters: {', '.join(a.get('limiters', [])) or '—'}",
        f"  Injury flags: {', '.join(a.get('injury_flags', [])) or 'None'}",
        "",
        f"🎯 *Goals*",
        f"  Primary: {g.get('primary_goal', '—')} (by {g.get('primary_goal_date', '—')})",
        f"  Weekly hours: {g.get('weekly_hour_budget', '—')}h",
    ]

    if g.get("events"):
        lines.append("  Events:")
        for e in g["events"]:
            lines.append(f"    - {e['name']}: {e['date']} [{e.get('priority', '?')}]")

    lines += [
        "",
        "📅 *Weekly availability*",
    ]
    for day, avail in av.items():
        lines.append(f"  {day}: {avail or '—'}")

    if parsed.get("notes"):
        lines += ["", f"📝 *Notes*: {parsed['notes']}"]

    lines += [
        "",
        "✅ Does this look correct? Reply *yes* to confirm, or tell me what needs changing.",
    ]
    return "\n".join(lines)
