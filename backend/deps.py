"""
FieldNotes — shared dependencies
"""
import secrets
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import Business


def verify_business_key(business_id: int, key: str, db: Session) -> Business:
    """Verify the per-business dashboard key. Raises 403/404 on failure."""
    biz = db.query(Business).filter(Business.id == business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")
    if not biz.dashboard_key or not secrets.compare_digest(biz.dashboard_key, key or ""):
        raise HTTPException(status_code=403, detail="Invalid dashboard key")
    return biz
