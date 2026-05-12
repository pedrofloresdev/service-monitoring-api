import logging
import time
import httpx
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models.services import Service
from app.db.models.metrics import Metric
from app.core.config import settings

logger = logging.getLogger(__name__)


def check_service(service: Service) -> dict:
    start = time.monotonic()
    try:
        with httpx.Client(timeout=settings.CHECK_TIMEOUT_SECONDS) as client:
            response = client.get(str(service.url))
        duration = round((time.monotonic() - start) * 1000, 2)
        status = "UP" if response.status_code == service.expected_status else "DOWN"
        return {
            "status": status,
            "status_code": response.status_code,
            "response_time_ms": duration,
            "error_message": None,
        }
    except Exception as exc:
        return {
            "status": "DOWN",
            "status_code": None,
            "response_time_ms": None,
            "error_message": str(exc)[:500],
        }


def check_service_by_id(service_id: int) -> None:
    db: Session = SessionLocal()
    try:
        service = db.query(Service).filter(Service.id == service_id, Service.is_active).first()
        if not service:
            return

        result = check_service(service)
        db.add(Metric(service_id=service.id, **result))
        db.commit()
        logger.info("Checked service %d (%s): %s in %.1fms", service.id, service.name, result["status"], result["response_time_ms"] or 0)
    except Exception:
        db.rollback()
        logger.exception("Error while checking service %d", service_id)
    finally:
        db.close()


def get_consecutive_failures(db: Session, service_id: int, limit: int = None) -> int:
    if limit is None:
        limit = settings.FAIL_THRESHOLD

    metrics = (
        db.query(Metric)
        .filter(Metric.service_id == service_id)
        .order_by(Metric.checked_at.desc())
        .limit(limit)
        .all()
    )

    failures = 0
    for m in metrics:
        if m.status == "DOWN":
            failures += 1
        else:
            break
    return failures
