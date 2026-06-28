"""
FieldNotes — Route Management Routes
Define scheduled stops for missed-stop detection.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..models import SessionLocal, RouteEntry, Account
from ..schemas import RouteEntryCreate, RouteEntryOut

router = APIRouter(prefix="/routes", tags=["routes"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", response_model=List[RouteEntryOut])
def list_routes(business_id: int, db: Session = Depends(get_db)):
    return db.query(RouteEntry).filter(
        RouteEntry.business_id == business_id,
        RouteEntry.is_active == True
    ).order_by(RouteEntry.day_of_week, RouteEntry.route_order).all()


@router.post("/", response_model=RouteEntryOut, status_code=201)
def create_route(data: RouteEntryCreate, business_id: int, db: Session = Depends(get_db)):
    # Verify account belongs to business
    account = db.query(Account).filter(
        Account.id == data.account_id,
        Account.business_id == business_id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    entry = RouteEntry(business_id=business_id, **data.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/{entry_id}", status_code=204)
def delete_route(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(RouteEntry).filter(RouteEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Route entry not found")
    entry.is_active = False
    db.commit()
    return None
