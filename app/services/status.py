from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from app.db.models.services import Service
from app.db.models.metrics import Metric
from app.services.monitor import get_consecutive_failures
from app.schemas.status import ServiceStatusEntry, SystemStatus
from app.core.config import settings


def get_services_status(db: Session) -> SystemStatus:
    services = db.query(Service).filter(Service.is_active).order_by(Service.created_at).all()

    entries: list[ServiceStatusEntry] = []
    up_count = 0
    down_count = 0

    for service in services:
        last_metric = (
            db.query(Metric)
            .filter(Metric.service_id == service.id)
            .order_by(Metric.checked_at.desc())
            .first()
        )

        if not last_metric:
            continue

        failures = get_consecutive_failures(db, service.id)
        status = "DOWN" if failures >= settings.FAIL_THRESHOLD else "UP"

        if status == "UP":
            up_count += 1
        else:
            down_count += 1

        avg_response = (
            db.query(func.avg(Metric.response_time_ms))
            .filter(Metric.service_id == service.id)
            .scalar()
        ) or 0.0

        since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        metrics_24h = (
            db.query(Metric)
            .filter(Metric.service_id == service.id, Metric.checked_at >= since_24h)
            .all()
        )

        total = len(metrics_24h)
        up_in_24h = sum(1 for m in metrics_24h if m.status == "UP")
        uptime = round(up_in_24h / total * 100, 2) if total > 0 else 0.0

        entries.append(ServiceStatusEntry(
            id=service.id,
            name=service.name,
            url=service.url,
            status=status,
            avg_response_time_ms=round(avg_response, 2),
            uptime_last_24h=uptime,
        ))

    return SystemStatus(
        total_services=len(entries),
        up=up_count,
        down=down_count,
        services=entries,
    )
