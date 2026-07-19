"""
FieldNotes — Daily Summary Routes
End-of-day digest and missed-stop detection.
"""
import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date
from typing import Optional

from ..models import SessionLocal, Business, Account, Worker, ServiceLog, Action, RouteEntry
from ..schemas import DailySummary
from ..integrations.email import send_daily_summary
from ..integrations.telegram import send_message
from ..deps import verify_business_key

router = APIRouter(prefix="/summary", tags=["summary"])

CRON_SECRET = os.getenv("FIELDNOTES_CRON_SECRET", "")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _build_today_summary(db: Session, business_id: int) -> DailySummary:
    """Generate today's summary: stops done, missed, issues, actions."""
    today = date.today().isoformat()

    # Stops completed today
    logs = db.query(ServiceLog).filter(
        ServiceLog.business_id == business_id,
        ServiceLog.timestamp >= today
    ).all()

    stops_completed = len(logs)

    # Issues flagged today
    issues_flagged = sum(1 for log in logs if log.parsed_status == "issues_found")

    # Workers active today
    worker_ids = set(log.worker_id for log in logs)
    workers = db.query(Worker).filter(Worker.id.in_(worker_ids)).all() if worker_ids else []
    worker_names = [w.name for w in workers]

    # Pending actions
    pending = db.query(Action).filter(
        Action.business_id == business_id,
        Action.status.in_(["pending", "in_progress"])
    ).count()

    # Supplies needed
    supply_actions = db.query(Action).filter(
        Action.business_id == business_id,
        Action.status.in_(["pending", "in_progress"]),
        Action.description.ilike("supply:%")
    ).all()
    supplies = [
        a.description.replace("Supply: ", "")
        for a in supply_actions
    ]

    # Missed stop detection
    today_dow = date.today().strftime("%A").lower()
    week_type = _get_week_type()  # "weekly", "week_a", "week_b"

    scheduled = db.query(RouteEntry).filter(
        RouteEntry.business_id == business_id,
        RouteEntry.is_active == True,
        RouteEntry.day_of_week == today_dow
    ).filter(
        (RouteEntry.week_type == "weekly") | (RouteEntry.week_type == week_type)
    ).all()

    logged_account_ids = set(log.account_id for log in logs if log.account_id)
    scheduled_account_ids = set(r.account_id for r in scheduled)
    missed_ids = scheduled_account_ids - logged_account_ids

    missed_names = []
    if missed_ids:
        missed_accounts = db.query(Account).filter(Account.id.in_(missed_ids)).all()
        missed_names = [a.name for a in missed_accounts]

    biz = db.query(Business).filter(Business.id == business_id).first()

    return DailySummary(
        date=today,
        business_name=biz.name if biz else "",
        stops_completed=stops_completed,
        stops_expected=len(scheduled_account_ids),
        stops_missed=missed_names,
        issues_flagged=issues_flagged,
        actions_pending=pending,
        supplies_needed=supplies,
        workers_active=worker_names
    )


def _format_summary_message(s: DailySummary) -> str:
    """Render a DailySummary as a Telegram HTML message for the owner."""
    lines = [f"📋 <b>{s.business_name} — Daily Summary</b> ({s.date})", ""]
    lines.append(f"🚐 Stops: <b>{s.stops_completed}</b> completed"
                 + (f" of {s.stops_expected} scheduled" if s.stops_expected else ""))
    if s.stops_missed:
        lines.append(f"❌ Missed: {', '.join(s.stops_missed)}")
    if s.issues_flagged:
        lines.append(f"⚠️ Issues flagged: {s.issues_flagged}")
    if s.actions_pending:
        lines.append(f"📌 Open actions: {s.actions_pending}")
    if s.supplies_needed:
        lines.append(f"🧰 Supplies: {', '.join(s.supplies_needed)}")
    if s.workers_active:
        lines.append(f"👷 Active today: {', '.join(s.workers_active)}")
    if s.stops_completed == 0 and not s.stops_expected:
        lines.append("Quiet day — no stops logged or scheduled.")
    return "\n".join(lines)


@router.get("/today", response_model=DailySummary)
def today_summary(business_id: int, key: str = "", db: Session = Depends(get_db)):
    """Today's summary for the dashboard (key-locked)."""
    verify_business_key(business_id, key, db)
    return _build_today_summary(db, business_id)


@router.post("/email")
async def email_summary(business_id: int, key: str = "", db: Session = Depends(get_db)):
    """Generate and email today's summary to the business owner."""
    business = verify_business_key(business_id, key, db)
    summary = _build_today_summary(db, business_id)
    result = await send_daily_summary(
        to_email=business.owner_email,
        summary_data=summary.model_dump()
    )
    return {"ok": True, "result": result}


@router.post("/send-daily")
async def send_daily(secret: str = "", business_id: Optional[int] = None,
                     db: Session = Depends(get_db)):
    """
    Nightly cron entry point: Telegram the daily summary to each owner
    who has linked their Telegram (owner_telegram_id set).
    Protected by FIELDNOTES_CRON_SECRET (env). Optionally scope to one business.
    """
    import secrets as _secrets
    if not CRON_SECRET or not _secrets.compare_digest(CRON_SECRET, secret or ""):
        raise HTTPException(status_code=403, detail="Invalid secret")

    q = db.query(Business).filter(
        Business.is_active == True,
        Business.owner_telegram_id.isnot(None),
        Business.owner_telegram_id != ""
    )
    if business_id:
        q = q.filter(Business.id == business_id)

    sent, failed = [], []
    for biz in q.all():
        try:
            summary = _build_today_summary(db, biz.id)
            result = await send_message(biz.owner_telegram_id,
                                        _format_summary_message(summary))
            if result.get("ok"):
                sent.append(biz.name)
            else:
                failed.append({"business": biz.name, "error": result})
        except Exception as e:
            failed.append({"business": biz.name, "error": str(e)})

    return {"ok": True, "sent": sent, "failed": failed}


def _get_week_type() -> str:
    """Determine if this is Week A or Week B based on ISO week number."""
    week_num = date.today().isocalendar()[1]
    return "week_a" if week_num % 2 == 0 else "week_b"
