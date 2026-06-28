"""
FieldNotes — Worker Routes
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..models import SessionLocal, Worker
from ..schemas import WorkerCreate, WorkerOut

router = APIRouter(prefix="/workers", tags=["workers"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", response_model=List[WorkerOut])
def list_workers(business_id: int, db: Session = Depends(get_db)):
    return db.query(Worker).filter(
        Worker.business_id == business_id,
        Worker.is_active == True
    ).all()


@router.post("/", response_model=WorkerOut, status_code=201)
def create_worker(data: WorkerCreate, business_id: int, db: Session = Depends(get_db)):
    worker = Worker(business_id=business_id, **data.model_dump())
    db.add(worker)
    db.commit()
    db.refresh(worker)
    return worker


@router.get("/{worker_id}", response_model=WorkerOut)
def get_worker(worker_id: int, db: Session = Depends(get_db)):
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    return worker


@router.delete("/{worker_id}", status_code=204)
def deactivate_worker(worker_id: int, db: Session = Depends(get_db)):
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    worker.is_active = False
    db.commit()
    return None
