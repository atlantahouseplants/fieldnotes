"""
FieldNotes — Account Routes
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..models import SessionLocal, Account
from ..schemas import AccountCreate, AccountOut
from ..deps import verify_business_key

router = APIRouter(prefix="/accounts", tags=["accounts"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_account_verified(account_id: int, key: str, db: Session) -> Account:
    """Fetch account and verify the caller holds its business's dashboard key."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    verify_business_key(account.business_id, key, db)
    return account


@router.get("/", response_model=List[AccountOut])
def list_accounts(business_id: int, key: str = "", db: Session = Depends(get_db)):
    verify_business_key(business_id, key, db)
    return db.query(Account).filter(
        Account.business_id == business_id,
        Account.is_active == True
    ).all()


@router.get("/usage")
def usage(business_id: int, key: str = "", db: Session = Depends(get_db)):
    """P5: usage metering for the owner dashboard (key-locked).
    Counts for the current calendar month + all-time, plus tier/features."""
    from datetime import datetime
    from ..models import Worker, ServiceLog, QaEvent
    from ..deps import FEATURE_TIERS, has_feature

    biz = verify_business_key(business_id, key, db)
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    qa_q = db.query(QaEvent).filter(QaEvent.business_id == business_id)
    qa_month = qa_q.filter(QaEvent.created_at >= month_start).all()
    logs_q = db.query(ServiceLog).filter(ServiceLog.business_id == business_id)

    return {
        "business": biz.name,
        "tier": biz.tier or "solo",
        "subscription_status": biz.subscription_status,
        "beta_all_access": bool(getattr(biz, "beta_all_access", False)),
        "features": {f: has_feature(biz, f) for f in FEATURE_TIERS},
        "usage": {
            "questions_this_month": len([e for e in qa_month if not (e.answer or "").startswith("[GATED:")]),
            "gated_attempts_this_month": len([e for e in qa_month if (e.answer or "").startswith("[GATED:")]),
            "questions_total": qa_q.count(),
            "logs_this_month": logs_q.filter(ServiceLog.timestamp >= month_start).count(),
            "logs_total": logs_q.count(),
            "workers": db.query(Worker).filter(Worker.business_id == business_id, Worker.is_active == True).count(),
            "accounts": db.query(Account).filter(Account.business_id == business_id, Account.is_active == True).count(),
        },
    }


@router.post("/", response_model=AccountOut, status_code=201)
def create_account(data: AccountCreate, business_id: int, key: str = "", db: Session = Depends(get_db)):
    verify_business_key(business_id, key, db)
    account = Account(business_id=business_id, **data.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: int, key: str = "", db: Session = Depends(get_db)):
    return _get_account_verified(account_id, key, db)


@router.put("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, data: AccountCreate, key: str = "", db: Session = Depends(get_db)):
    account = _get_account_verified(account_id, key, db)
    for k, val in data.model_dump(exclude_unset=True).items():
        setattr(account, k, val)
    db.commit()
    db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=204)
def deactivate_account(account_id: int, key: str = "", db: Session = Depends(get_db)):
    account = _get_account_verified(account_id, key, db)
    account.is_active = False
    db.commit()
    return None
