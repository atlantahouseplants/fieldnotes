"""Post-webhook deterministic execution pipeline."""
from sqlalchemy.orm import Session
from .action_queue import add_action



def run_pipeline(db: Session, log: "ServiceLog") -> dict:
    """Run all deterministic checks after a service log is created."""
    res = {"supply_actions": 0, "followup_actions": 0, "issue_actions": 0}

    # Process parsed supplies
    supplies = log.get_supplies()
    for s in supplies:
        r = add_action(db, log.business_id, f"Supply: {s}",
                       priority="next_visit", account_id=log.account_id or 0,
                       service_log_id=log.id)
        if r["created"]:
            res["supply_actions"] += 1

    # Process parsed follow-ups
    followups = log.get_followups()
    for f in followups:
        r = add_action(db, log.business_id, f,
                       priority="next_visit", account_id=log.account_id or 0,
                       service_log_id=log.id)
        if r["created"]:
            res["followup_actions"] += 1

    # Process parsed issues
    issues = log.get_issues()
    for i in issues:
        r = add_action(db, log.business_id, i,
                       priority="this_week", account_id=log.account_id or 0,
                       service_log_id=log.id)
        if r["created"]:
            res["issue_actions"] += 1

    return {"pipeline": res}


def daily_summary(db: Session, business_id: int) -> dict:
    """Generate end-of-day summary."""
    from ..models import ServiceLog
    from datetime import datetime, timedelta

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    logs = (
        db.query(ServiceLog)
        .filter(
            ServiceLog.business_id == business_id,
            ServiceLog.timestamp >= today,
        )
        .order_by(ServiceLog.timestamp.desc())
        .all()
    )

    completed = [l for l in logs if l.parsed_status == "all_good"]
    with_issues = [l for l in logs if l.parsed_status not in ("all_good", "")]

    return {
        "date": today.isoformat(),
        "stops_completed": len(completed),
        "stops_with_issues": len(with_issues),
        "total_stops": len(logs),
        "completed": [{"account": l.account_name(), "time": l.timestamp.isoformat() if l.timestamp else ""} for l in completed],
        "issues": [{"account": l.account_name(), "status": l.parsed_status} for l in with_issues],
    }
