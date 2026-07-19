"""
FieldNotes — Worker Routes
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..models import SessionLocal, Worker
from ..schemas import WorkerCreate, WorkerOut
from ..deps import verify_business_key

router = APIRouter(prefix="/workers", tags=["workers"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_worker_verified(worker_id: int, key: str, db: Session) -> Worker:
    """Fetch worker and verify the caller holds its business's dashboard key."""
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    verify_business_key(worker.business_id, key, db)
    return worker


@router.get("/", response_model=List[WorkerOut])
def list_workers(business_id: int, key: str = "", db: Session = Depends(get_db)):
    verify_business_key(business_id, key, db)
    return db.query(Worker).filter(
        Worker.business_id == business_id,
        Worker.is_active == True
    ).all()


@router.post("/", response_model=WorkerOut, status_code=201)
def create_worker(data: WorkerCreate, business_id: int, key: str = "", db: Session = Depends(get_db)):
    verify_business_key(business_id, key, db)
    worker = Worker(business_id=business_id, **data.model_dump())
    db.add(worker)
    db.commit()
    db.refresh(worker)
    return worker


@router.get("/{worker_id}", response_model=WorkerOut)
def get_worker(worker_id: int, key: str = "", db: Session = Depends(get_db)):
    return _get_worker_verified(worker_id, key, db)


@router.delete("/{worker_id}", status_code=204)
def deactivate_worker(worker_id: int, key: str = "", db: Session = Depends(get_db)):
    worker = _get_worker_verified(worker_id, key, db)
    worker.is_active = False
    db.commit()
    return None
