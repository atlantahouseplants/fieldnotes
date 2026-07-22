"""
FieldNotes — Telegram Webhook Route
Receives worker messages, parses them, logs service, creates actions, sends confirmation.
"""
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import json
import os
import secrets as _secrets

TELEGRAM_SECRET = os.getenv("TELEGRAM_SECRET", "")

from ..models import SessionLocal, Worker, Account, ServiceLog, Business
from ..services.parser import parse_note
from ..services.actions import create_action, bulk_create_actions
from ..services.action_queue import add_action as queue_add
from ..services.ahp_pipeline import run_pipeline
from ..services.ingest import persist_parsed_note
from ..services.qa import looks_like_question, answer_question, route_intent
from ..integrations.telegram import send_confirmation, send_message
from ..deps import has_feature, upgrade_message

router = APIRouter(prefix="/webhook", tags=["webhook"])


def _record_gated_attempt(db: Session, business_id: int, worker, question: str, feature: str) -> None:
    """P5 telemetry: record gated feature attempts as upgrade-intent signal.
    Never let telemetry break the reply path."""
    from ..models import QaEvent
    try:
        ev = QaEvent(
            business_id=business_id,
            worker_id=worker.id if worker else None,
            question=question,
            answer=f"[GATED:{feature}] upgrade prompt shown",
            sources=json.dumps({"gated": True, "feature": feature}),
            created_at=datetime.utcnow(),
        )
        db.add(ev)
        db.commit()
    except Exception:
        db.rollback()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/telegram/status")
async def webhook_status():
    """Quick check that webhook endpoint is alive."""
    return {"status": "ready", "service": "FieldNotes Telegram Webhook"}


@router.post("/telegram")
async def telegram_webhook(request: Request):
    """
    Receive Telegram messages, process worker notes.
    
    Flow:
    1. Receive message from Telegram
    2. Identify worker by Telegram ID
    3. Parse note with AI
    4. Match account name
    5. Create service log
    6. Generate action items
    7. Send confirmation to worker
    """
    # Verify the update actually came from Telegram (secret_token set at setWebhook)
    if TELEGRAM_SECRET:
        sent = request.headers.get("x-telegram-bot-api-secret-token", "")
        if not _secrets.compare_digest(sent, TELEGRAM_SECRET):
            raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()
    
    # Extract message from Telegram update
    message = body.get("message", {})
    if not message:
        return {"ok": True, "detail": "no message in update"}
    
    chat = message.get("chat", {})
    text = message.get("text", "")
    telegram_id = str(chat.get("id", ""))
    
    if not text or not telegram_id:
        return {"ok": True, "detail": "no text or chat id"}

    # Handle /start commands (deep-link worker registration + welcome)
    if text.startswith("/start"):
        db = SessionLocal()
        try:
            return await handle_start(db, telegram_id, text, chat)
        finally:
            db.close()

    # Process in a fresh DB session
    db = SessionLocal()
    try:
        result = await process_worker_note(db, telegram_id, text)
        return {"ok": True, **result}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def handle_start(db: Session, telegram_id: str, text: str, chat: dict) -> dict:
    """Handle /start — deep-link worker registration or plain welcome."""
    parts = text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    first_name = chat.get("first_name") or chat.get("username") or "there"

    if payload.startswith("owner_"):
        token = payload[len("owner_"):]
        biz = db.query(Business).filter(Business.invite_token == token).first()
        if not biz:
            await send_message(telegram_id, "⚠️ That owner link isn't valid. Generate a fresh one from your dashboard.")
            return {"ok": True, "detail": "invalid_owner_link"}

        biz.owner_telegram_id = telegram_id
        db.commit()

        await send_message(
            telegram_id,
            f"👑 You're linked as the owner of <b>{biz.name}</b>!\n\n"
            f"Every evening I'll send you a daily summary here: stops completed, "
            f"missed stops, issues flagged, and open actions.\n\n"
            f"Your dashboard: https://fieldnotesapp.io/app/dashboard.html?biz={biz.id}&key={biz.dashboard_key}"
        )
        return {"ok": True, "detail": "owner_linked", "business": biz.name}

    if payload.startswith("invite_"):
        token = payload[len("invite_"):]
        biz = db.query(Business).filter(Business.invite_token == token).first()
        if not biz:
            await send_message(telegram_id, "⚠️ That invite link isn't valid. Ask your boss for a fresh one.")
            return {"ok": True, "detail": "invalid_invite"}

        worker = db.query(Worker).filter(Worker.telegram_id == telegram_id).first()
        if worker:
            worker.business_id = biz.id
            worker.is_active = True
        else:
            worker = Worker(business_id=biz.id, name=first_name, telegram_id=telegram_id)
            db.add(worker)
        db.commit()

        await send_message(
            telegram_id,
            f"🎉 You're connected to <b>{biz.name}</b>!\n\n"
            f"After each stop, just send me a quick message like:\n"
            f"<i>\"Acme Office: all good, replaced filter, need more filters next time.\"</i>\n\n"
            f"Voice notes work too. 10 seconds, done. 🎙️"
        )
        return {"ok": True, "detail": "worker_registered", "business": biz.name}

    # Plain /start — no invite
    await send_message(
        telegram_id,
        f"👋 Hey {first_name}! I'm the <b>FieldNotes</b> bot.\n\n"
        f"Field crews send me 10-second voice notes between stops — "
        f"I turn them into service logs, action queues, and daily summaries for the boss.\n\n"
        f"<b>Trying the demo?</b> Just send me a note like:\n"
        f"<i>\"Acme Office: serviced, all good. River Towers: pump leaking, need parts.\"</i>\n\n"
        f"<b>Have an invite link from your boss?</b> Tap it to connect to your company.\n"
        f"<b>Want FieldNotes for your crew?</b> https://fieldnotesapp.io"
    )
    return {"ok": True, "detail": "welcome_sent"}


async def process_worker_note(db: Session, telegram_id: str, text: str) -> dict:
    """Process a single worker note end-to-end."""

    # 1. Find worker
    worker = db.query(Worker).filter(
        Worker.telegram_id == telegram_id,
        Worker.is_active == True
    ).first()

    is_demo = False
    if not worker:
        # DEMO MODE: Allow unregistered users to test with the demo business
        demo_worker = db.query(Worker).filter(
            Worker.business_id == 2,  # Precision HVAC demo
            Worker.is_active == True
        ).first()
        if demo_worker:
            worker = demo_worker
            is_demo = True
        else:
            return {"detail": "unknown_worker", "telegram_id": telegram_id}

    business_id = int(worker.business_id)

    # ── Ask FieldNotes: questions get answers, not service logs ──
    if looks_like_question(text):
        # P5: feature gate — route questions need Crew, other Q&A needs Team.
        # Beta tenants (beta_all_access) pass everything. Gated attempts are
        # recorded in qa_events as upgrade-intent signal (no answer given).
        biz = db.query(Business).filter(Business.id == business_id).first()
        feature = "routes" if route_intent(text) else "qa"
        if biz and not has_feature(biz, feature):
            msg = upgrade_message(feature, biz)
            await send_message(telegram_id, msg)
            _record_gated_attempt(db, business_id, worker, text, feature)
            return {
                "worker": worker.name,
                "intent": "question",
                "gated": True,
                "feature": feature,
                "answer": msg,
            }
        qa = await answer_question(db, business_id, worker, text)
        await send_message(telegram_id, qa["answer"])
        if is_demo:
            await send_message(
                telegram_id,
                "🧪 <i>Demo mode — answered from our sample business, Precision HVAC. "
                "Want this for your crew? https://fieldnotesapp.io</i>"
            )
        return {
            "worker": worker.name,
            "intent": "question",
            "answer": qa["answer"],
            "sources": qa["sources"],
            "clarification": qa["clarification"],
        }

    # 2. Get known accounts for matching
    accounts = db.query(Account).filter(
        Account.business_id == business_id,
        Account.is_active == True
    ).all()
    account_hints = [a.shorthand or a.name for a in accounts]
    
    # Build mapping: shorthand/name → account.id
    account_map = {}
    for a in accounts:
        account_map[a.name.lower()] = a.id
        if a.shorthand:
            account_map[a.shorthand.lower()] = a.id
    
    # 3. Parse note with AI
    parsed = await parse_note(text, account_hints)
    
    # 4. Match account
    account_hint = (parsed.get("account_hint") or "").lower()
    account_id = None
    account_name = account_hint or text[:40]
    
    # Try exact match first
    if account_hint in account_map:
        account_id = account_map[account_hint]
        account_name = account_hint
    
    # If still no match, leave as uncategorized — owner can re-assign
    if not account_id:
        account_name = account_hint if account_hint else "uncategorized"
    
    # 5-7. Persist through the SHARED pipeline (services/ingest.py):
    # ServiceLog + action items + deterministic execution pipeline.
    # Owner-added dashboard notes go through this exact same function.
    persisted = persist_parsed_note(
        db,
        business_id=business_id,
        worker_id=int(worker.id),
        text=text,
        parsed=parsed,
        account_id=account_id,
    )
    log = persisted["log"]
    actions_created = persisted["actions_created"]
    pipeline_result = persisted["pipeline"]

    # 8. Send confirmation to worker
    await send_confirmation(
        chat_id=telegram_id,
        account_name=account_name,
        status=parsed.get("status", "logged")
    )

    # Demo disclosure — unregistered users are testing the sample business
    if is_demo:
        await send_message(
            telegram_id,
            "🧪 <i>Demo mode — logged to our sample business, Precision HVAC. "
            "Want this for your crew? https://fieldnotesapp.io</i>"
        )
    
    return {
        "worker": worker.name,
        "account": account_name,
        "account_id": account_id,
        "status": parsed.get("status"),
        "summary": parsed.get("summary", text[:100]),
        "actions_created": actions_created,
        "processing_ms": parsed.get("processing_time_ms", 0),
        "pipeline": pipeline_result,
    }
