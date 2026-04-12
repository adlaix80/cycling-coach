"""
cli.py
A command-line interface for testing the coaching agent and
for use without Telegram (e.g. running on a desktop or server).

Usage:
  python cli.py chat          # Start a conversational session
  python cli.py briefing      # Trigger a manual daily briefing
  python cli.py status        # Print current training status
  python cli.py plan          # Show this week's sessions
  python cli.py review        # Trigger weekly review
  python cli.py onboard       # Re-run onboarding
  python cli.py upload <file> # Upload and parse a training dossier
"""

import sys
import os
from datetime import date
from dotenv import load_dotenv

load_dotenv()

from engine.training_state import TrainingState
from engine.coach_engine import CoachEngine
from engine.dossier_parser import parse_dossier, format_extraction_for_confirmation


def print_separator(char="─", width=60):
    print(char * width)


def cmd_chat(training_state: TrainingState):
    """Interactive chat session with the coach."""
    engine = CoachEngine(training_state)
    print("\n🚴 AI Cycling Coach — Chat Mode")
    print_separator()
    print("Type your message and press Enter. Type 'quit' or 'exit' to stop.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nSession ended.")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            print("Session ended.")
            break

        if not user_input:
            continue

        print("\nCoach: ", end="", flush=True)
        try:
            reply = engine.chat(user_input)
            print(reply)
        except Exception as e:
            print(f"[Error] {e}")
        print()


def cmd_briefing(training_state: TrainingState):
    """Trigger a daily briefing and print to stdout."""
    from interfaces.daily_briefing import run_daily_briefing
    print("\n🌅 Triggering daily briefing...\n")
    run_daily_briefing(training_state=training_state, send_fn=print)


def cmd_status(training_state: TrainingState):
    """Print current training status."""
    pmc = training_state.get_pmc_summary()
    ftp_trend = training_state.get_ftp_trend()
    block = training_state.state["current_block"]
    athlete = training_state.state["athlete"]
    goals = training_state.state["goals"]

    print("\n📊 Training Status\n")
    print_separator()
    print(f"Athlete: {athlete.get('name', '—')}  |  FTP: {ftp_trend.get('current')}W  |  "
          f"VO2max: {athlete.get('vo2max')} ml/kg/min")
    print(f"W/kg: {round(ftp_trend['current']/athlete['weight_kg'], 2) if ftp_trend.get('current') and athlete.get('weight_kg') else '—'}")
    print()
    print(f"CTL (Fitness):  {pmc.get('ctl')}")
    print(f"ATL (Fatigue):  {pmc.get('atl')}")
    print(f"TSB (Form):     {pmc.get('tsb')}")
    print()
    print(f"FTP trend — 4w: {ftp_trend.get('change_4w')}W  |  12w: {ftp_trend.get('change_12w')}W")
    print()
    print_separator()
    print(f"Block: {block.get('type', '—').title()}  |  Week {block.get('week_number')} of {block.get('total_weeks')}")
    print(f"Target weekly TSS: {block.get('target_tss_week')}")
    print()
    print_separator()
    print(f"Primary goal: {goals.get('primary_goal')} (by {goals.get('primary_goal_date')})")


def cmd_plan(training_state: TrainingState):
    """Show this week's training sessions."""
    sessions = training_state.get_this_weeks_sessions()
    if not sessions:
        print("\nNo sessions planned. Ask the coach to generate a training block.")
        return

    print(f"\n📅 This Week's Training Plan\n")
    print_separator()

    for s in sessions:
        status = "✅" if s.get("status") == "complete" else "○"
        print(f"\n{status} {s['day'].upper()} — {s['title']}")
        print(f"   Type: {s.get('type')} | Duration: {s.get('duration_min')}min | TSS: ~{s.get('tss_estimate')}")
        if s.get("description"):
            print(f"   {s['description'][:200]}")
        if s.get("main_set"):
            print(f"   Main set: {s['main_set'][:150]}")


def cmd_review(training_state: TrainingState):
    """Trigger a weekly review."""
    from interfaces.weekly_review import run_weekly_review
    print("\n📊 Triggering weekly review...\n")
    run_weekly_review(training_state=training_state, send_fn=print)


def cmd_upload(training_state: TrainingState, file_path: str):
    """Parse and apply a training dossier document."""
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return

    print(f"\nParsing {file_path}...")
    try:
        parsed = parse_dossier(file_path)
    except Exception as e:
        print(f"Parse error: {e}")
        return

    print("\n" + format_extraction_for_confirmation(parsed))
    confirm = input("\nApply this data? (yes/no): ").strip().lower()

    if confirm in ("yes", "y"):
        if parsed.get("athlete"):
            training_state.state["athlete"].update(parsed["athlete"])
        if parsed.get("goals"):
            training_state.state["goals"].update(parsed["goals"])
        if parsed.get("availability"):
            training_state.state["availability"].update(parsed["availability"])
        if parsed.get("ftp_history"):
            training_state.state["ftp_history"].extend(parsed["ftp_history"])
        training_state.save()
        print("\n✅ Profile updated.")

        gen = input("Generate a new training block now? (yes/no): ").strip().lower()
        if gen in ("yes", "y"):
            engine = CoachEngine(training_state)
            print("Generating 4-week block...")
            try:
                plan = engine.generate_training_block(weeks=4)
                training_state.update_block({
                    "type": plan.get("block_type"),
                    "total_weeks": plan.get("total_weeks"),
                    "week_number": 1,
                    "start_date": date.today().isoformat(),
                    "target_tss_week": plan.get("target_weekly_tss"),
                    "sessions": plan["weeks"][0]["sessions"] if plan.get("weeks") else [],
                })
                print(f"\n✅ {plan.get('block_type').title()} block generated.")
                print(f"Rationale: {plan.get('rationale')}")
                print("\nUse 'python cli.py plan' to see this week's sessions.")
            except Exception as e:
                print(f"Block generation error: {e}")
    else:
        print("Cancelled.")


def cmd_onboard(training_state: TrainingState):
    """Run interactive CLI onboarding."""
    engine = CoachEngine(training_state)
    history = []

    print("\n🚴 AI Cycling Coach — Onboarding\n")
    print_separator()
    print("I'll ask you a series of questions to build your athlete profile.")
    print("Type 'done' when you're finished.\n")

    # Start the conversation
    initial = engine.onboarding_chat(
        "Please start by introducing yourself and asking for my first set of details.",
        []
    )
    print(f"Coach: {initial}\n")
    history.append({"role": "assistant", "content": initial})

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.lower() == "done":
            break

        history.append({"role": "user", "content": user_input})
        reply = engine.onboarding_chat(user_input, history)
        history.append({"role": "assistant", "content": reply})

        if "ONBOARDING_COMPLETE" in reply:
            reply = reply.replace("ONBOARDING_COMPLETE", "").strip()
            print(f"\nCoach: {reply}\n")
            print("\n✅ Onboarding complete. Generating your training block...")

            # Extract and save profile
            import anthropic as _anthropic, json
            client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            conv = "\n".join(f"{h['role'].upper()}: {h['content']}" for h in history)
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1500,
                messages=[{"role": "user", "content": f"""Extract athlete profile from this conversation as JSON.
Return ONLY the JSON object with keys: athlete, goals, availability.
Conversation:\n{conv}"""}]
            )
            try:
                profile = json.loads(response.content[0].text.strip())
                if profile.get("athlete"):
                    training_state.state["athlete"].update(profile["athlete"])
                if profile.get("goals"):
                    training_state.state["goals"].update(profile["goals"])
                if profile.get("availability"):
                    training_state.state["availability"].update(profile["availability"])
                training_state.complete_onboarding()
                training_state.save()
                print("Profile saved. Run 'python cli.py plan' to see your first week.")
            except Exception as e:
                print(f"Profile extraction error: {e}")
            break
        else:
            print(f"\nCoach: {reply}\n")


def main():
    training_state = TrainingState()
    args = sys.argv[1:]

    if not args or args[0] == "chat":
        cmd_chat(training_state)
    elif args[0] == "briefing":
        cmd_briefing(training_state)
    elif args[0] == "status":
        cmd_status(training_state)
    elif args[0] == "plan":
        cmd_plan(training_state)
    elif args[0] == "review":
        cmd_review(training_state)
    elif args[0] == "onboard":
        cmd_onboard(training_state)
    elif args[0] == "upload":
        if len(args) < 2:
            print("Usage: python cli.py upload <file_path>")
        else:
            cmd_upload(training_state, args[1])
    else:
        print(f"""
AI Cycling Coach CLI

Usage:
  python cli.py chat          Interactive chat with your coach
  python cli.py briefing      Trigger today's briefing
  python cli.py status        Current CTL/ATL/TSB and FTP trend
  python cli.py plan          This week's training sessions
  python cli.py review        Trigger weekly review
  python cli.py onboard       Re-run onboarding interview
  python cli.py upload <file> Upload and parse a training dossier
""")


if __name__ == "__main__":
    main()
