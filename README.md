# FieldNotes

Voice-first field service logging. Workers talk to Telegram. AI processes everything. Owners get daily summaries.

## Quick Start

```bash
cd backend
pip install -r requirements.txt
python3 -m uvicorn main:app --reload
```

Dashboard: http://localhost:8765/app/dashboard.html

## Setup

1. Copy `.env.example` to `.env` and fill in keys
2. Create a Telegram bot via [@BotFather](https://t.me/BotFather)
3. Set the webhook: `curl -X POST http://localhost:8765/webhook/telegram/status`

## Architecture

```
Worker → Telegram bot → Webhook → AI Parser → Service Log → Daily Summary → Owner Email
                                                ↓
                                          Action Queue
```

## API

| Endpoint | Description |
|----------|-------------|
| `POST /businesses/` | Create business account |
| `GET /accounts/?business_id=` | List accounts |
| `POST /accounts/?business_id=` | Create account |
| `GET /workers/?business_id=` | List workers |
| `POST /workers/?business_id=` | Add worker |
| `POST /webhook/telegram` | Receive worker notes |
| `GET /logs/today?business_id=` | Today's service logs |
| `GET /summary/today?business_id=` | Daily summary |
| `POST /summary/email?business_id=` | Email summary to owner |
| `GET /routes/?business_id=` | Scheduled routes |

## Env Vars

| Key | Required | Purpose |
|-----|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token |
| `DEEPSEEK_API_KEY` | Recommended | AI note parsing ($0.14/M tokens) |
| `OPENAI_API_KEY` | Optional | Fallback AI parsing |
| `RESEND_API_KEY` | Optional | Email summaries |
| `PUBLIC_URL` | Yes | Webhook URL for Telegram |
