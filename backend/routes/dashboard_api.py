from fastapi import APIRouter, Query, HTTPException
from ..models import SessionLocal, ServiceLog, Account, Worker, Business

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/logs")
def dashboard_logs(business_id: int = Query(...), key: str = Query(...), limit: int = Query(20)):
    db = SessionLocal()
    try:
        biz = db.query(Business).filter(Business.id == business_id).first()
        if not biz or not biz.dashboard_key or biz.dashboard_key != key:
            raise HTTPException(status_code=403, detail="Invalid dashboard key")

        logs = db.query(ServiceLog).filter(
            ServiceLog.business_id == business_id
        ).order_by(ServiceLog.timestamp.desc()).limit(limit).all()

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
