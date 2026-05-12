from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Optional
from app.db.session import get_db
from app.db.models.services import Service
from app.db.models.metrics import Metric
from app.schemas.metrics import MetricResponse, MetricsPage

NAME = "metrics"
PREFIX = f"/{NAME}"
router = APIRouter(prefix=PREFIX, tags=[NAME])


@router.get("/services/{service_id}", response_model=MetricsPage)
def get_service_metrics(
    service_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    hours: Optional[int] = Query(default=None, ge=1, le=8760, description="Restrict to last N hours"),
    db: Session = Depends(get_db),
):
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    query = db.query(Metric).filter(Metric.service_id == service_id)

    if hours is not None:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = query.filter(Metric.checked_at >= since)

    total = query.count()
    items = query.order_by(Metric.checked_at.desc()).offset(offset).limit(limit).all()

    return MetricsPage(
        service_id=service_id,
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )
