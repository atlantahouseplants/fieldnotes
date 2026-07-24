import json
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import SessionLocal, ServiceLog, Account, Worker, Business
from ..deps import verify_business_key, has_feature
from ..services.schedule import parse_schedule, sync_route_entries
from ..services.accounts import create_account, get_or_create_owner_worker
from ..services.qa import _match_accounts
from ..services.parser import parse_note
from ..services.ingest import persist_parsed_note
from ..services import recaps as recaps_mod
from ..integrations.telegram import send_message
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

    # ONE create path, shared with the owner chat command (P6b)
    account, err = create_account(
        db, business_id, req.name, address=req.address,
        gate_code=req.gate_code, access_notes=req.access_notes,
        schedule=req.schedule)
    if err:
        return {"ok": False, "message": err}
    return {"ok": True, "message": "Customer added ✓", "account_id": account.id}


class AddNoteRequest(BaseModel):
    account: str
    note: str


# Canonical home: services/accounts.py (shared with the P6b chat path)
_get_or_create_owner_worker = get_or_create_owner_worker


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

    # P8: owner notes start recaps too — same pipeline, same approval flow.
    if persisted.get("recap") is not None:
        biz = db.query(Business).filter(Business.id == business_id).first()
        if biz:
            await recaps_mod.draft_and_notify(db, biz, persisted["recap"], send_message)

    return {"ok": True, "message": f"Note added to {account.name} ✓", "log_id": int(persisted["log"].id)}


# ── P8: client recap controls (owner dashboard) ──────────────────
class SetRecapRequest(BaseModel):
    account_id: int
    enabled: bool
    email: Optional[str] = None


@router.post("/set-recap")
async def set_recap(
    req: SetRecapRequest,
    business_id: int = Query(...),
    key: str = Query(...),
    db: Session = Depends(get_db)
):
    biz = verify_business_key(business_id, key, db)
    if not has_feature(biz, "recaps"):
        return {"ok": False, "message": "Client recaps are on the Team plan — upgrade to turn them on."}
    account = db.query(Account).filter(
        Account.id == req.account_id,
        Account.business_id == business_id,   # tenant scope
        Account.is_active == True,
    ).first()
    if not account:
        return {"ok": False, "message": "Customer not found."}
    if req.enabled:
        email = (req.email or account.recap_email or "").strip().lower()
        if not recaps_mod._EMAIL_RE.match(email):
            return {"ok": False, "message": "Add the client's email first — recaps need somewhere to go."}
        account.recap_enabled = True
        account.recap_email = email
        db.commit()
        return {"ok": True, "message": f"Recaps on for {account.name} ✓ — you'll approve each one before it sends."}
    account.recap_enabled = False
    db.commit()
    return {"ok": True, "message": f"Recaps off for {account.name}."}


# ── P7: account tasks ─────────────────────────────────────────────
class AddTaskRequest(BaseModel):
    account: str
    title: str
    details: Optional[str] = None
    supplies: Optional[str] = None


def _task_dict(t, acct_names: dict, worker_names: dict) -> dict:
    return {
        "id": int(t.id),
        "account": acct_names.get(int(t.account_id), "?"),
        "title": t.title,
        "details": t.details,
        "supplies": t.supplies_needed,
        "due": t.due_date,
        "status": t.status,
        "assigned_to": worker_names.get(t.assigned_worker_id),
        "created_at": str(t.created_at),
        "closed_at": str(t.closed_at) if t.closed_at else None,
    }


@router.get("/tasks")
def list_tasks(
    business_id: int = Query(...),
    key: str = Query(...),
    db: Session = Depends(get_db),
):
    """Open tasks + the 10 most recently closed, tenant-scoped, key-locked."""
    verify_business_key(business_id, key, db)
    from ..models import AccountTask
    from ..services.tasks import open_tasks_for_business
    open_t = open_tasks_for_business(db, business_id)
    closed = db.query(AccountTask).filter(
        AccountTask.business_id == business_id,
        AccountTask.status == "done",
    ).order_by(AccountTask.closed_at.desc()).limit(10).all()
    acct_names = {a.id: a.name for a in db.query(Account).filter(
        Account.business_id == business_id).all()}
    worker_names = {w.id: w.name for w in db.query(Worker).filter(
        Worker.business_id == business_id).all()}
    return {"ok": True,
            "open": [_task_dict(t, acct_names, worker_names) for t in open_t],
            "closed": [_task_dict(t, acct_names, worker_names) for t in closed]}


@router.post("/add-task")
async def add_task(
    req: AddTaskRequest,
    business_id: int = Query(...),
    key: str = Query(...),
    db: Session = Depends(get_db),
):
    verify_business_key(business_id, key, db)
    from ..services import tasks as tasks_mod

    if not req.title.strip():
        return {"ok": False, "message": "Type the task first"}

    all_accounts = db.query(Account).filter(
        Account.business_id == business_id, Account.is_active == True).all()
    q = req.account.strip().lower()
    exact = [a for a in all_accounts
             if a.name.lower() == q or (a.shorthand and a.shorthand.lower() == q)]
    matched = exact or _match_accounts(req.account, all_accounts)
    if not matched:
        return {"ok": False, "message": f"Couldn't find a customer called \"{req.account}\" — add them first."}
    if len(matched) > 1:
        names = ", ".join(a.name for a in matched)
        return {"ok": False, "message": f"Which customer? Could be: {names}"}

    account = matched[0]
    task = tasks_mod.create_task(
        db, business_id=business_id, account_id=int(account.id),
        title=req.title, details=req.details, supplies=req.supplies,
        source="dashboard", created_by_worker_id=None,  # null = owner created
    )
    return {"ok": True, "message": f"Task added to {account.name} ✓", "task_id": int(task.id)}


@router.post("/close-task")
async def close_task_endpoint(
    task_id: int = Query(...),
    business_id: int = Query(...),
    key: str = Query(...),
    db: Session = Depends(get_db),
):
    verify_business_key(business_id, key, db)
    from ..models import AccountTask
    from ..services import tasks as tasks_mod

    task = db.query(AccountTask).filter(
        AccountTask.id == task_id,
        AccountTask.business_id == business_id,  # tenant scope — never close across tenants
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "open":
        return {"ok": False, "message": "That task is already closed"}
    tasks_mod.close_task(db, task, closed_by_worker_id=None)  # owner closed it
    return {"ok": True, "message": "Task marked done ✓"}


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


class SmsInviteRequest(BaseModel):
    name: str
    phone: str


@router.post("/sms-invite")
async def sms_invite(
    req: SmsInviteRequest,
    business_id: int = Query(...),
    key: str = Query(...),
    db: Session = Depends(get_db),
):
    """P3 — owner adds a worker by phone: pending (inactive) Worker row +
    invite SMS with opt-out language. Worker's YES reply flips them live
    (handled in routes/webhook.py process_sms). One business per phone."""
    from ..integrations.agentphone import send_sms, normalize_e164

    biz = verify_business_key(business_id, key, db)
    e164 = normalize_e164(req.phone)
    if not e164:
        return {"ok": False, "message": "That phone number doesn't look right — use a full US number."}
    name_clean = (req.name or "").strip() or "Crew"

    # One business per phone: an ACTIVE worker anywhere blocks the number…
    active = db.query(Worker).filter(
        Worker.phone == e164, Worker.is_active == True).first()
    if active:
        if int(active.business_id) == business_id:
            return {"ok": False, "message": f"{active.name} is already connected from that number."}
        return {"ok": False, "message": "That number is already connected to a FieldNotes crew."}

    # …and so does a PENDING invite held by another tenant. Without this,
    # two businesses could hold pending rows for the same number and a YES
    # would register into whichever query won.
    pending = db.query(Worker).filter(
        Worker.phone == e164, Worker.business_id == business_id).first()
    if pending and pending.sms_opted_out:
        # 10DLC: never send to an opted-out number — they must text START first.
        return {"ok": False,
                "message": "That number replied STOP before — they have to text START to our number first."}
    if not pending:
        foreign_pending = db.query(Worker).filter(
            Worker.phone == e164, Worker.business_id != business_id).first()
        if foreign_pending:
            if foreign_pending.sms_opted_out:
                return {"ok": False,
                        "message": "That number replied STOP before — they have to text START to our number first."}
            return {"ok": False,
                    "message": "That number already has a pending invite to another crew — they can reply YES to that text, or STOP then START."}
        db.add(Worker(business_id=business_id, name=name_clean,
                      phone=e164, is_active=False))
    else:
        pending.name = name_clean
    db.commit()

    res = await send_sms(
        e164,
        f"{biz.name} added you to FieldNotes — text job notes to this number "
        f"and they go straight to the office. Reply YES to join. "
        f"Msg&data rates may apply. Reply STOP to opt out.")
    if not res.get("ok"):
        return {"ok": False,
                "message": f"SMS send failed ({res.get('error', 'unknown')}) — try again in a minute."}
    return {"ok": True,
            "message": f"Invite texted to {name_clean} at {e164} — they're live when they reply YES."}


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
