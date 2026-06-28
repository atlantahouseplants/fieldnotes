"""
FieldNotes — Daily Summary Routes
End-of-day digest and missed-stop detection.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date, datetime
from typing import Optional

from ..models import SessionLocal, Business, Account, Worker, ServiceLog, Action, RouteEntry
from ..schemas import DailySummary
from ..integrations.email import send_daily_summary

router = APIRouter(prefix="/summary", tags=["summary"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/today", response_model=DailySummary)
def today_summary(business_id: int, db: Session = Depends(get_db)):
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
    
    # If no logs today and no scheduled stops, this might be an off-day
    stops_expected = len(scheduled_account_ids)
    
    return DailySummary(
        date=today,
        business_name="",
        stops_completed=stops_completed,
        stops_expected=stops_expected,
        stops_missed=missed_names,
        issues_flagged=issues_flagged,
        actions_pending=pending,
        supplies_needed=supplies,
        workers_active=worker_names
    )


@router.post("/email")
async def email_summary(business_id: int, db: Session = Depends(get_db)):
    """Generate and email today's summary to the business owner."""
    business = db.query(Business).filter(Business.id == business_id).first()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    
    # Get summary data
    summary = today_summary(business_id=business_id, db=db)
    
    # Send email
    result = await send_daily_summary(
        to_email=business.owner_email,
        summary_data=summary.model_dump()
    )
    
    return {"ok": True, "result": result}


def _get_week_type() -> str:
    """Determine if this is Week A or Week B based on ISO week number."""
    week_num = date.today().isocalendar()[1]
    return "week_a" if week_num % 2 == 0 else "week_b"
