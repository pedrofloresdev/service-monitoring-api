import logging
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.metrics import Metric
from app.db.models.services import Service
from app.db.session import SessionLocal
from app.services import url_safety

logger = logging.getLogger(__name__)


def _failed(message: str) -> dict[str, Any]:
    return {
        "status": "DOWN",
        "status_code": None,
        "response_time_ms": None,
        "error_message": message[:500],
    }


def _destination_problem(host: str) -> str | None:
    """Why no request may be sent to `host` in this check, or None (spec 002 RF-6 to RF-9)."""
    host_class = url_safety.classify_host(host)
    if host_class in (url_safety.HostClass.RESERVED, url_safety.HostClass.UNSAFE_LITERAL):
        return url_safety.UNSAFE_LITERAL_MESSAGE
    if host_class is url_safety.HostClass.PUBLIC_LITERAL:
        return None
    resolution = url_safety.resolve_host(host, settings.RESOLUTION_TIMEOUT_SECONDS)
    if not resolution.addresses:
        return f"{url_safety.RESOLUTION_FAILED_PREFIX} {host}: {resolution.error}"
    if any(not url_safety.is_public_address(a) for a in resolution.addresses):
        return url_safety.UNSAFE_RESOLVED_MESSAGE
    return None


def check_service(service: Service) -> dict[str, Any]:
    start = time.monotonic()
    try:
        url = httpx.URL(str(service.url))
        problem = _destination_problem(url_safety.normalize_host(url.raw_host.decode("ascii")))
        if problem is not None:
            return _failed(problem)
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
        return _failed(str(exc))


def check_service_by_id(service_id: int) -> None:
    db: Session = SessionLocal()
    try:
        service = db.query(Service).filter(Service.id == service_id, Service.is_active).first()
        if not service:
            return

        result = check_service(service)
        db.add(Metric(service_id=service.id, **result))
        db.commit()
        logger.info(
            "Checked service %d (%s): %s in %.1fms",
            service.id,
            service.name,
            result["status"],
            result["response_time_ms"] or 0,
        )
    except Exception:
        db.rollback()
        logger.exception("Error while checking service %d", service_id)
    finally:
        db.close()


def get_consecutive_failures(db: Session, service_id: int, limit: int | None = None) -> int:
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
