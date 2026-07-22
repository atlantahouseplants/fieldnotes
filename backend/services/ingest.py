"""
Shared note-ingestion pipeline — the ONE path a raw note becomes a parsed
ServiceLog + action-queue entries + deterministic pipeline side effects.

Used by:
- backend/routes/webhook.py (rep notes from Telegram)
- backend/routes/dashboard_api.py (owner notes from the dashboard)

Rule (P6 spec, binding): owner-added notes go THROUGH the same pipeline —
no raw ServiceLog inserts, no special cases.
"""
import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import ServiceLog
from .actions import create_action
from .ahp_pipeline import run_pipeline


def persist_parsed_note(
    db: Session,
    *,
    business_id: int,
    worker_id: int,
    text: str,
    parsed: dict,
    account_id: int | None,
) -> dict:
    """Create the ServiceLog + action items + run the deterministic pipeline.

    `parsed` is the dict returned by services.parser.parse_note.
    Returns {"log": ServiceLog, "actions_created": [str], "pipeline": dict}.
    """
    log = ServiceLog(
        business_id=business_id,
        account_id=account_id or None,  # Allow uncategorized
        worker_id=worker_id,
        raw_note=text,
        parsed_status=parsed.get("status", ""),
        parsed_issues=json.dumps(parsed.get("issues", [])),
        parsed_supplies=json.dumps(parsed.get("supplies", [])),
        parsed_followups=json.dumps(parsed.get("followups", [])),
        parsed_customer_requests=json.dumps(parsed.get("customer_requests", [])),
        timestamp=datetime.utcnow(),
        processing_time_ms=parsed.get("processing_time_ms", 0),
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    actions_created = []

    for issue in parsed.get("issues", []):
        action = create_action(
            db=db, business_id=business_id,
            description=issue, priority="this_week",
            account_id=account_id or 0,
            service_log_id=int(log.id), source="service_log",
        )
        actions_created.append(action.description)

    for supply in parsed.get("supplies", []):
        action = create_action(
            db=db, business_id=business_id,
            description=f"Supply: {supply}", priority="next_visit",
            account_id=account_id or 0,
            service_log_id=int(log.id), source="service_log",
        )
        actions_created.append(action.description)

    for followup in parsed.get("followups", []):
        action = create_action(
            db=db, business_id=business_id,
            description=followup, priority="next_visit",
            account_id=account_id or 0,
            service_log_id=int(log.id), source="service_log",
        )
        actions_created.append(action.description)

    pipeline_result = run_pipeline(db, log)

    return {"log": log, "actions_created": actions_created, "pipeline": pipeline_result}
