"""
FieldNotes — Telegram Webhook Route
Receives worker messages, parses them, logs service, creates actions, sends confirmation.
"""
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import json

from ..models import SessionLocal, Worker, Account, ServiceLog
from ..services.parser import parse_note
from ..services.actions import create_action, bulk_create_actions
from ..services.action_queue import add_action as queue_add
from ..services.ahp_pipeline import run_pipeline
from ..integrations.telegram import send_confirmation

router = APIRouter(prefix="/webhook", tags=["webhook"])


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
    
    # Process in a fresh DB session
    db = SessionLocal()
    try:
        result = await process_worker_note(db, telegram_id, text)
        return {"ok": True, **result}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def process_worker_note(db: Session, telegram_id: str, text: str) -> dict:
    """Process a single worker note end-to-end."""
    
    # 1. Find worker
    worker = db.query(Worker).filter(
        Worker.telegram_id == telegram_id,
        Worker.is_active == True
    ).first()
    
    if not worker:
        # DEMO MODE: Allow unregistered users to test with the demo business
        demo_worker = db.query(Worker).filter(
            Worker.business_id == 2,  # Precision HVAC demo
            Worker.is_active == True
        ).first()
        if demo_worker:
            worker = demo_worker
        else:
            return {"detail": "unknown_worker", "telegram_id": telegram_id}
    
    business_id = worker.business_id
    
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
    
    # 5. Create service log (use None for uncategorized accounts)
    log = ServiceLog(
        business_id=business_id,
        account_id=account_id or None,  # Allow uncategorized
        worker_id=worker.id,
        raw_note=text,
        parsed_status=parsed.get("status", ""),
        parsed_issues=json.dumps(parsed.get("issues", [])),
        parsed_supplies=json.dumps(parsed.get("supplies", [])),
        parsed_followups=json.dumps(parsed.get("followups", [])),
        parsed_customer_requests=json.dumps(parsed.get("customer_requests", [])),
        timestamp=datetime.utcnow(),
        processing_time_ms=parsed.get("processing_time_ms", 0)
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    
    # 6. Create action items
    actions_created = []
    
    for issue in parsed.get("issues", []):
        action = create_action(
            db=db, business_id=business_id,
            description=issue, priority="this_week",
            account_id=account_id or 0,
            service_log_id=log.id, source="service_log"
        )
        actions_created.append(action.description)
    
    for supply in parsed.get("supplies", []):
        action = create_action(
            db=db, business_id=business_id,
            description=f"Supply: {supply}", priority="next_visit",
            account_id=account_id or 0,
            service_log_id=log.id, source="service_log"
        )
        actions_created.append(action.description)
    
    for followup in parsed.get("followups", []):
        action = create_action(
            db=db, business_id=business_id,
            description=followup, priority="next_visit",
            account_id=account_id or 0,
            service_log_id=log.id, source="service_log"
        )
        actions_created.append(action.description)
    
    # 7. Run deterministic execution pipeline
    pipeline_result = run_pipeline(db, log)

    # 8. Send confirmation to worker
    await send_confirmation(
        chat_id=telegram_id,
        account_name=account_name,
        status=parsed.get("status", "logged")
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
