"""
FieldNotes — Action Queue Management
Deterministic action item management with deduplication and priority tiers.
"""
from sqlalchemy.orm import Session
from ..models import Action
from datetime import datetime
from typing import List


PRIORITIES = ["urgent", "this_week", "next_visit", "ongoing"]


def add_action(
    db: Session,
    business_id: int,
    description: str,
    priority: str = "this_week",
    account_id: int = None,
    service_log_id: int = None,
    source: str = "service_log",
) -> dict:
    """
    Add an action item with deduplication.
    Returns {"created": True|False, "action": Action|None}
    """
    # Dedup: same description within same business
    existing = (
        db.query(Action)
        .filter(
            Action.business_id == business_id,
            Action.description == description,
            Action.status != "completed",
        )
        .first()
    )
    if existing:
        return {"created": False, "action": existing}

    action = Action(
        business_id=business_id,
        account_id=account_id or 0,
        description=description,
        priority=priority,
        service_log_id=service_log_id,
        source=source,
        created_at=datetime.utcnow(),
        status="pending",
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return {"created": True, "action": action}


def get_action_queue(db: Session, business_id: int) -> dict:
    """Get full action queue organized by priority."""
    result = {}
    for pri in PRIORITIES:
        items = (
            db.query(Action)
            .filter(
                Action.business_id == business_id,
                Action.priority == pri,
                Action.status == "pending",
            )
            .order_by(Action.created_at.desc())
            .all()
        )
        result[pri] = [
            {
                "id": a.id,
                "description": a.description,
                "account_id": a.account_id,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in items
        ]
    return result


def mark_completed(db: Session, action_id: int):
    """Mark an action as completed."""
    action = db.query(Action).filter(Action.id == action_id).first()
    if action:
        action.status = "completed"
        db.commit()
