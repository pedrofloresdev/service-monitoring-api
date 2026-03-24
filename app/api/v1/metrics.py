from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models.metrics import Metric

NAME = "metrics"
PREFIX = f"/{NAME}"
router = APIRouter(prefix=PREFIX, tags=[NAME])


@router.get("/services/{service_id}")
def get_service_metrics(service_id: int, db: Session = Depends(get_db)):
    service = db.query(Metric).filter(Metric.service_id == service_id).all()
    return service
