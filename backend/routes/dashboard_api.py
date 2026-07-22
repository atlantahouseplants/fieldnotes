import json
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import SessionLocal, ServiceLog, Account, Worker, Business
from ..deps import verify_business_key
from ..services.schedule import parse_schedule, sync_route_entries
from ..services.qa import _match_accounts
from ..services.parser import parse_note
from ..services.ingest import persist_parsed_note
from .onboarding import BOT_USERNAME as TELEGRAM_BOT_USERNAME

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class AddAccountRequest(BaseModel):
    name: str
    address: Optional[str] = None
    gate_code: Optional[str] = None
    access_notes: Optional[str] = None
    schedule: Optional[str] = None


@router.post("/add-account")
async def add_account(
    req: AddAccountRequest,
    business_id: int = Query(...),
    key: str = Query(...),
    db: Session = Depends(get_db)
):
    biz = verify_business_key(business_id, key, db)

    name_clean = req.name.strip()
    if not name_clean:
        return {"ok": False, "message": "Type the customer's name first"}

    existing = db.query(Account).filter(
        Account.business_id == business_id,
        func.lower(Account.name) == name_clean.lower()
    ).first()
    if existing:
        return {"ok": False, "message": f"You already have a customer called \"{existing.name}\" — no need to add it twice."}

    account = Account(
        business_id=business_id,
        name=name_clean,
        address=req.address,
        gate_code=req.gate_code,
        access_notes=req.access_notes,
        schedule=req.schedule,
        is_active=True
    )
    db.add(account)
    db.flush() # To get account.id

    if req.schedule:
        parsed_schedule = parse_schedule(req.schedule)
        account.schedule_parsed = json.dumps(parsed_schedule) if (parsed_schedule["entries"] or parsed_schedule["monthly_day"]) else None
        sync_route_entries(db, business_id) # Sync all accounts for this business

    db.commit()
    db.refresh(account)

    return {"ok": True, "message": "Customer added ✓", "account_id": account.id}


class AddNoteRequest(BaseModel):
    account: str
    note: str


def _get_or_create_owner_worker(db: Session, business_id: int) -> Worker:
    """Attribution row for owner-added notes (ServiceLog.worker_id is NOT NULL).
    telegram_id stays NULL — owner isn't a field worker; morning-push and
    summary worker loops skip NULL/placeholder ids (isdigit check)."""
    owner = db.query(Worker).filter(
        Worker.business_id == business_id, Worker.name == "Owner"
    ).first()
    if not owner:
        owner = Worker(business_id=business_id, name="Owner", telegram_id=None, is_active=True)
        db.add(owner)
        db.flush()  # id available now; commit happens with the note in persist_parsed_note
        db.refresh(owner)
    return owner


@router.post("/add-note")
async def add_note(
    req: AddNoteRequest,
    business_id: int = Query(...),
    key: str = Query(...),
    db: Session = Depends(get_db)
):
    verify_business_key(business_id, key, db)

    if not req.note.strip():
        return {"ok": False, "message": "Type a note first"}

    # Match the account: exact name/shorthand first, then the word-boundary
    # matcher shared with Q&A (its first-word alias can over-match on
    # near-duplicate names, e.g. "Smith Office" → Smith Tower).
    all_accounts = db.query(Account).filter(
        Account.business_id == business_id, Account.is_active == True
    ).all()
    q = req.account.strip().lower()
    exact = [a for a in all_accounts
             if a.name.lower() == q or (a.shorthand and a.shorthand.lower() == q)]
    matched = exact or _match_accounts(req.account, all_accounts)

    if not matched:
        return {"ok": False, "message": f"Couldn't find a customer called \"{req.account}\" — add them first, then attach the note."}
    if len(matched) > 1:
        names = ", ".join(a.name for a in matched)
        return {"ok": False, "message": f"Which customer? Could be: {names}"}

    account = matched[0]
    owner_worker = _get_or_create_owner_worker(db, business_id)

    # ONE pipeline, no special cases: same AI parse + persist path rep notes use
    hints = [account.name] + ([account.shorthand] if account.shorthand else [])
    parsed = await parse_note(req.note, hints)
    persisted = persist_parsed_note(
        db,
        business_id=business_id,
        worker_id=int(owner_worker.id),
        text=req.note,
        parsed=parsed,
        account_id=int(account.id),
    )

    return {"ok": True, "message": f"Note added to {account.name} ✓", "log_id": int(persisted["log"].id)}


@router.get("/invite-link")
def invite_link(
    business_id: int = Query(...),
    key: str = Query(...),
    db: Session = Depends(get_db)
):
    biz = verify_business_key(business_id, key, db)

    if not biz.invite_token:
        biz.invite_token = secrets.token_urlsafe(12)
        db.commit()
        db.refresh(biz)
    
    invite_link_url = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=invite_{biz.invite_token}"
    owner_link_url = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=owner_{biz.invite_token}"

    return {"ok": True, "invite_link": invite_link_url, "owner_link": owner_link_url}


@router.get("/logs")
def dashboard_logs(business_id: int = Query(...), key: str = Query(...), limit: int = Query(20)):
    db = SessionLocal()
    try:
        biz = db.query(Business).filter(Business.id == business_id).first()
        if not biz or not biz.dashboard_key or biz.dashboard_key != key:
            raise HTTPException(status_code=403, detail="Invalid dashboard key")

        logs = db.query(ServiceLog).filter(
            ServiceLog.business_id == business_id
        ).order_by(ServiceLog.timestamp.desc()).limit(limit).all()

        result = []
        for log in logs:
            account = db.query(Account).filter(
                Account.id == log.account_id
            ).first() if log.account_id else None
            worker = db.query(Worker).filter(
                Worker.id == log.worker_id
            ).first() if log.worker_id else None

            result.append({
                "id": log.id,
                "worker": worker.name if worker else "Unknown",
                "account": account.name if account else (
                    log.raw_note[:30] if log.raw_note else "Uncategorized"
                ),
                "raw_note": log.raw_note,
                "status": log.parsed_status or "all_good",
                "issues": log.parsed_issues or "[]",
                "supplies": log.parsed_supplies or "[]",
                "followups": log.parsed_followups or "[]",
                "timestamp": str(log.timestamp),
                "processing_ms": log.processing_time_ms or 0,
            })
        return result
    finally:
        db.close()
