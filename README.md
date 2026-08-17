# TG-BT — Multi-Session Telegram Bot Manager

Web tool for splitting text across multiple Telegram sessions, sending commands to a target bot, capturing validated results, and forwarding them to a group.

## Features

- **15-line chunk splitting** — Input text is split into 15-line chunks with no line reuse
- **Multi-session distribution** — Chunks are assigned round-robin across selected accounts; each session processes its chunks sequentially
- **Result capture** — Parses `CC`, `Status`, `Response`, and optional `Receipt` blocks; non-matching results are marked failed and not shared
- **Group forwarding** — Valid results are sent to a configured Telegram group
- **Session management** — Upload `.session` files or login via phone on the web UI
- **AntiSpam retry** — Detects "Please Try Again After N Seconds" and retries automatically
- **Completion detection** — Waits for `Took: X.XXs | Proxy : Live ⛅️ User : ...` before moving to the next chunk

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your TELEGRAM_API_ID and TELEGRAM_API_HASH from https://my.telegram.org
```

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open http://localhost:8080

## Usage

1. **Config** — Set API ID/Hash, defaults for bot, command, and target group
2. **Sessions** — Upload `.session` files or login with phone
3. **Process** — Paste text, select sessions, set bot/group, preview split, start
4. **Live Status** — Watch found/failed/forwarded counts in real time via WebSocket

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_API_ID` | Telegram API ID |
| `TELEGRAM_API_HASH` | Telegram API Hash |
| `SESSION_DIR` | Directory for session files (default: `./sessions`) |
| `PORT` | Web server port (default: 8080) |
