from fastapi import APIRouter, Query
from ..models import SessionLocal, ServiceLog, Account, Worker

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/logs")
def dashboard_logs(limit: int = Query(20)):
    db = SessionLocal()
    try:
        logs = db.query(ServiceLog).order_by(
            ServiceLog.timestamp.desc()
        ).limit(limit).all()

        result = []
        for log in logs:
            account = db.query(Account).filter(
                Account.id == log.account_id
            ).first() if log.account_id else None
            worker = db.query(Worker).filter(
                Worker.id == log.worker_id
            ).first() if log.worker_id else None

            result.append({
                "id": log.id,
                "worker": worker.name if worker else "Unknown",
                "account": account.name if account else (
                    log.raw_note[:30] if log.raw_note else "Uncategorized"
                ),
                "raw_note": log.raw_note,
                "status": log.parsed_status or "all_good",
                "issues": log.parsed_issues or "[]",
                "supplies": log.parsed_supplies or "[]",
                "followups": log.parsed_followups or "[]",
                "timestamp": str(log.timestamp),
                "processing_ms": log.processing_time_ms or 0,
            })
        return result
    finally:
        db.close()
