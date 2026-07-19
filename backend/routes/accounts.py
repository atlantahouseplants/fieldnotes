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
