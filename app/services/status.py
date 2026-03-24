from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from app.db.models.services import Service
from app.db.models.metrics import Metric


def get_services_status(db: Session):
    services = db.query(Service).all()

    result = []
    up_count = 0
    down_count = 0

    for service in services:
        # Última métrica
        last_metric = (
            db.query(Metric)
            .filter(Metric.service_id == service.id)
            .order_by(Metric.checked_at.desc())
            .first()
        )

        if not last_metric:
            continue

        status = last_metric.status

        if status == "UP":
            up_count += 1
        else:
            down_count += 1

        # Promedio response time
        avg_response = (
            db.query(func.avg(Metric.response_time_ms))
            .filter(Metric.service_id == service.id)
            .scalar()
        )

        # Últimas 24h
        last_24h = datetime.now(timezone.utc) - timedelta(hours=24)

        metrics_24h = (
            db.query(Metric)
            .filter(
                Metric.service_id == service.id,
                Metric.checked_at >= last_24h
            )
            .all()
        )

        total = len(metrics_24h)
        up = len([m for m in metrics_24h if m.status == "UP"])

        uptime = (up / total * 100) if total > 0 else 0

        result.append({
            "name": service.name,
            "status": status,
            "avg_response_time": int(avg_response or 0),
            "uptime_last_24h": round(uptime, 2)
        })

    return {
        "total_services": len(result),
        "up": up_count,
        "down": down_count,
        "services": result
    }
