import logging
import ssl
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _Destination:
    problem: str | None = None  # why no request may be sent (spec 002 RF-7 to RF-9)
    addresses: tuple[url_safety.IPAddress, ...] = ()  # validated addresses, in attempt order
    pinned: bool = False  # True when the addresses come from resolving a hostname


def _destination(host: str) -> _Destination:
    """Decide where this check may connect, before any request (spec 002 RF-6 to RF-9)."""
    host_class = url_safety.classify_host(host)
    if host_class in (url_safety.HostClass.RESERVED, url_safety.HostClass.UNSAFE_LITERAL):
        return _Destination(problem=url_safety.UNSAFE_LITERAL_MESSAGE)
    if host_class is url_safety.HostClass.PUBLIC_LITERAL:
        literal = url_safety.parse_literal(host)
        assert literal is not None
        return _Destination(addresses=(literal,))
    resolution = url_safety.resolve_host(host, settings.RESOLUTION_TIMEOUT_SECONDS)
    if not resolution.addresses:
        return _Destination(
            problem=f"{url_safety.RESOLUTION_FAILED_PREFIX} {host}: {resolution.error}"
        )
    if any(not url_safety.is_public_address(a) for a in resolution.addresses):
        return _Destination(problem=url_safety.UNSAFE_RESOLVED_MESSAGE)
    return _Destination(addresses=resolution.addresses, pinned=True)


def _caused_by_tls(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _send(
    client: httpx.Client, url: httpx.URL, host: str, destination: _Destination
) -> httpx.Response:
    """Connect only to validated addresses, sharing one connect budget (RF-10 to RF-12).

    Pinned requests keep the original hostname for the Host header, TLS SNI and
    certificate verification (RF-11). Only TCP-level connect failures move on to the
    next address; a TLS failure ends the check, as before spec 002 (RNF-1).
    """
    host_header = httpx.Request("GET", url).headers["Host"]
    budget = float(settings.CHECK_TIMEOUT_SECONDS)
    last_error: Exception | None = None
    for address in destination.addresses:
        if budget <= 0:
            break
        target = url.copy_with(host=str(address)) if destination.pinned else url
        extensions = {"sni_hostname": host} if destination.pinned and url.scheme == "https" else {}
        attempt_start = time.monotonic()
        try:
            return client.get(
                target,
                headers={"Host": host_header},
                extensions=extensions,
                timeout=httpx.Timeout(settings.CHECK_TIMEOUT_SECONDS, connect=budget),
            )
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            if _caused_by_tls(exc):
                raise
            last_error = exc
            budget -= time.monotonic() - attempt_start
    raise last_error or httpx.ConnectTimeout("connect budget exhausted")


def check_service(service: Service) -> dict[str, Any]:
    start = time.monotonic()  # response time includes resolution, as before (RNF-1)
    try:
        url = httpx.URL(str(service.url))
        host = url_safety.normalize_host(url.raw_host.decode("ascii"))
        destination = _destination(host)
        if destination.problem is not None:
            return _failed(destination.problem)
        # No proxies or .netrc from the environment, but custom CA settings
        # (SSL_CERT_FILE / SSL_CERT_DIR) are still honored (RF-14). No redirects (RF-13).
        verify = httpx.create_ssl_context(trust_env=True)
        with httpx.Client(
            trust_env=False,
            verify=verify,
            follow_redirects=False,
            timeout=settings.CHECK_TIMEOUT_SECONDS,
        ) as client:
            response = _send(client, url, host, destination)
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
