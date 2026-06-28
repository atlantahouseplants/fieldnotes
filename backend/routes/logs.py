"""
FieldNotes — Service Log Routes
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date

from ..models import SessionLocal, ServiceLog, Account, Worker
from ..schemas import ServiceLogOut

router = APIRouter(prefix="/logs", tags=["logs"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", response_model=List[ServiceLogOut])
def list_logs(
    business_id: int,
    account_id: Optional[int] = None,
    worker_id: Optional[int] = None,
    date_str: Optional[str] = Query(None, alias="date"),
    limit: int = 50,
    db: Session = Depends(get_db)
):
    q = db.query(ServiceLog).filter(ServiceLog.business_id == business_id)

    if account_id:
        q = q.filter(ServiceLog.account_id == account_id)
    if worker_id:
        q = q.filter(ServiceLog.worker_id == worker_id)
    if date_str:
        d = date.fromisoformat(date_str)
        q = q.filter(ServiceLog.timestamp >= d.isoformat())
        q = q.filter(ServiceLog.timestamp < f"{d}T23:59:59")

    return q.order_by(ServiceLog.timestamp.desc()).limit(limit).all()


@router.get("/today", response_model=List[ServiceLogOut])
def today_logs(business_id: int, db: Session = Depends(get_db)):
    """Get all logs for today."""
    today = date.today().isoformat()
    return db.query(ServiceLog).filter(
        ServiceLog.business_id == business_id,
        ServiceLog.timestamp >= today
    ).order_by(ServiceLog.timestamp.asc()).all()


@router.get("/{log_id}", response_model=ServiceLogOut)
def get_log(log_id: int, db: Session = Depends(get_db)):
    log = db.query(ServiceLog).filter(ServiceLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
    return log
