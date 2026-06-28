"""
FieldNotes — Action Queue Service
Manages action items: create, prioritize, deduplicate, complete.
"""
from sqlalchemy.orm import Session
from typing import List, Optional
from ..models import Action, ActionPriority, ActionStatus


def create_action(
    db: Session,
    business_id: int,
    description: str,
    priority: str = "this_week",
    account_id: Optional[int] = None,
    service_log_id: Optional[int] = None,
    source: str = "service_log"
) -> Action:
    """Create an action item — deduplicates by description for same account."""
    # Check for existing similar action
    existing = db.query(Action).filter(
        Action.business_id == business_id,
        Action.description == description,
        Action.account_id == account_id,
        Action.status.in_(["pending", "in_progress"])
    ).first()
    
    if existing:
        return existing  # Don't create duplicates
    
    action = Action(
        business_id=business_id,
        account_id=account_id,
        service_log_id=service_log_id,
        description=description,
        priority=priority,
        source=source
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def get_pending_actions(db: Session, business_id: int) -> List[Action]:
    """Get all pending/in-progress actions, ordered by priority."""
    priority_order = {"urgent": 0, "this_week": 1, "next_visit": 2}
    
    actions = db.query(Action).filter(
        Action.business_id == business_id,
        Action.status.in_(["pending", "in_progress"])
    ).all()
    
    return sorted(actions, key=lambda a: priority_order.get(a.priority, 99))


def complete_action(db: Session, action_id: int) -> Optional[Action]:
    """Mark an action as completed."""
    import datetime
    action = db.query(Action).filter(Action.id == action_id).first()
    if action:
        action.status = ActionStatus.completed.value
        action.completed_at = datetime.datetime.utcnow()
        db.commit()
        db.refresh(action)
    return action


def cancel_action(db: Session, action_id: int) -> Optional[Action]:
    """Cancel an action."""
    action = db.query(Action).filter(Action.id == action_id).first()
    if action:
        action.status = ActionStatus.cancelled.value
        db.commit()
        db.refresh(action)
    return action


def bulk_create_actions(
    db: Session,
    business_id: int,
    items: list[dict],
    account_id: Optional[int] = None,
    service_log_id: Optional[int] = None,
    source: str = "service_log"
) -> List[Action]:
    """Create multiple actions from a parsed service log."""
    created = []
    for item in items:
        description = item.get("description", item.get("issue", item.get("supply", str(item))))
        priority = item.get("priority", "this_week")
        
        action = create_action(
            db=db,
            business_id=business_id,
            description=description,
            priority=priority,
            account_id=account_id,
            service_log_id=service_log_id,
            source=source
        )
        created.append(action)
    return created
