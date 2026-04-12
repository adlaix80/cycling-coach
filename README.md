# AI Cycling Coach

A conversational AI cycling coach powered by Claude, with automated daily data ingestion from Strava and Garmin Connect.

## Features

- **Onboarding**: Upload your training dossier (PDF/Word/text) and the coach extracts your history, goals, and current fitness
- **Daily automation**: Pulls yesterday's Strava activity + Garmin metrics every morning, generates a coached briefing
- **Conversational**: Chat via Telegram — ask questions, update availability, change goals anytime
- **State persistence**: Full memory across sessions — the coach always knows where you are in your training block
- **Progress tracking**: Tracks FTP and VO2max trends week-over-week, adjusts programming automatically

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required credentials:
- **Strava**: Create an app at https://www.strava.com/settings/api
- **Garmin**: Your Garmin Connect username and password
- **Telegram**: Create a bot via @BotFather on Telegram, get the token
- **Anthropic**: Your API key from https://console.anthropic.com

### 3. Strava OAuth setup

Run the one-time Strava auth flow:

```bash
python setup_strava.py
```

This opens a browser, you authorise, and the refresh token is saved automatically.

### 4. First run — onboarding

```bash
python main.py --onboard
```

If you have a training dossier document, place it in `data/dossier/` before running.
The Telegram bot will guide you through the rest interactively.

### 5. Start the agent

```bash
python main.py
```

This starts both the daily scheduler (07:00 every morning) and the Telegram bot listener.

### Running as a background service (Linux/Mac)

```bash
nohup python main.py > logs/coach.log 2>&1 &
```

Or use the provided systemd service file: `cycling-coach.service`

## Project Structure

```
cycling-coach/
├── main.py                    # Entry point + scheduler
├── setup_strava.py            # One-time Strava OAuth flow
├── config.yaml                # Coaching parameters and thresholds
├── .env.example               # Credentials template
├── requirements.txt
├── clients/
│   ├── strava_client.py       # Strava API integration
│   └── garmin_client.py       # Garmin Connect integration
├── engine/
│   ├── coach_engine.py        # Claude API + prompt builder
│   ├── training_state.py      # CTL/ATL/TSB calculator + state manager
│   ├── plan_builder.py        # Generates and updates weekly sessions
│   └── dossier_parser.py      # Ingests uploaded training documents
├── interfaces/
│   ├── telegram_bot.py        # Conversational interface + onboarding
│   └── daily_briefing.py      # Morning automated push
└── data/
    ├── coaching_state.json     # Persistent state (auto-created)
    ├── activity_log.json       # Activity history (auto-created)
    └── dossier/               # Place your training document here
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Current training block, CTL/ATL/TSB, FTP trend |
| `/plan` | This week's prescribed sessions |
| `/update` | Update availability or goals |
| `/upload` | Re-process your training dossier |
| `/metrics` | Latest FTP and VO2max readings |
| `/help` | Show all commands |

Or just chat naturally — the coach understands plain language.
