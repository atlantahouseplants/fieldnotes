# FieldNotes — Build Context

## What We're Building
FieldNotes: A SaaS that lets field service workers dictate voice/text notes between stops via Telegram. AI processes them into structured service logs, action queues, and daily owner summaries. No apps, no forms, no training.

## Tech Stack
- **Backend:** Python + FastAPI
- **Database:** SQLite (MVP) → PostgreSQL/Supabase (scale)
- **AI:** LLM API for note parsing (OpenAI/Anthropic/DeepSeek)
- **Messaging:** Telegram Bot API
- **Frontend:** Simple web dashboard (React or HTMX)
- **Email:** Resend API
- **Hosting:** Vercel or small VPS

## Project Structure
```
fieldnotes/
├── PRODUCT_SPEC.md          # Full product spec (read this first)
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── models.py            # SQLAlchemy models
│   ├── schemas.py           # Pydantic schemas
│   ├── routes/              # API routes
│   │   ├── accounts.py      # Account CRUD
│   │   ├── messages.py      # Telegram webhook
│   │   ├── logs.py          # Service logs
│   │   └── summary.py       # Daily summaries
│   ├── services/            # Business logic
│   │   ├── parser.py        # AI note parsing
│   │   ├── actions.py       # Action queue management
│   │   └── alerts.py        # Missed stop detection
│   ├── integrations/
│   │   ├── telegram.py      # Telegram Bot API
│   │   └── email.py         # Resend integration
│   └── requirements.txt
├── frontend/                # Web dashboard (MVP: keep simple)
└── README.md
```

## MVP Scope (30 days)
See PRODUCT_SPEC.md for full details. Key features:
1. Telegram webhook receives voice/text notes
2. AI parses account name + extracts issues/supplies/follow-ups
3. System creates service log, updates action queues
4. Daily summary email to owner
5. Simple web dashboard showing today's activity
6. Multi-worker support (tag notes by worker)

## Starting Point
1. Read PRODUCT_SPEC.md thoroughly
2. Scaffold the backend with FastAPI + SQLite
3. Get Telegram webhook working first (message in → system receives)
4. Then wire up AI parsing
5. Then build output layer (dashboard + email)

## The Reference System
Geoff already runs this exact workflow for Atlanta Houseplants via Hermes Agent. The scripts at `/home/wallg/.hermes/skills/business/ahp-route-capture/scripts/` are the proof of concept — deterministic service logging, account updates, replacement tracking, and action queue management. Study those for the execution layer design.
