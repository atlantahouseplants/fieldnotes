"""
FieldNotes — Account Routes
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..models import SessionLocal, Account
from ..schemas import AccountCreate, AccountOut

router = APIRouter(prefix="/accounts", tags=["accounts"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", response_model=List[AccountOut])
def list_accounts(business_id: int, db: Session = Depends(get_db)):
    return db.query(Account).filter(
        Account.business_id == business_id,
        Account.is_active == True
    ).all()


@router.post("/", response_model=AccountOut, status_code=201)
def create_account(data: AccountCreate, business_id: int, db: Session = Depends(get_db)):
    account = Account(business_id=business_id, **data.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.put("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, data: AccountCreate, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(account, key, val)
    db.commit()
    db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=204)
def deactivate_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account.is_active = False
    db.commit()
    return None
