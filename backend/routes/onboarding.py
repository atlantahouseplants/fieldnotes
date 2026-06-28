"""
FieldNotes — Business Onboarding Routes
Create/manage business accounts.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import re

from ..models import SessionLocal, Business
from ..schemas import BusinessCreate, BusinessOut

router = APIRouter(prefix="/businesses", tags=["businesses"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


@router.post("/", response_model=BusinessOut, status_code=201)
def create_business(data: BusinessCreate, db: Session = Depends(get_db)):
    slug = slugify(data.name)
    
    # Ensure unique slug
    existing = db.query(Business).filter(Business.slug == slug).first()
    if existing:
        slug = f"{slug}-{existing.id + 1}" if existing else slug
    
    biz = Business(
        name=data.name,
        slug=slug,
        owner_email=data.owner_email,
        owner_name=data.owner_name,
        phone=data.phone
    )
    db.add(biz)
    db.commit()
    db.refresh(biz)
    return biz


@router.get("/{business_id}", response_model=BusinessOut)
def get_business(business_id: int, db: Session = Depends(get_db)):
    biz = db.query(Business).filter(Business.id == business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")
    return biz
