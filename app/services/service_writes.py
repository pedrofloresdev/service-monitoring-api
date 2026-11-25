"""Create, update and delete flows for services (spec 003a; spec 002 checks kept).

Every check runs before anything is changed, in the order of spec 003a RF-16. A rejected
write leaves the service untouched. The routes schedule or unschedule only after these
functions return, i.e. after a successful commit.
"""

from __future__ import annotations

import threading
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import demo_settings
from app.core.demo_settings import DemoConfig
from app.db.models.services import Service, ServiceOrigin
from app.schemas.services import ServiceCreate, ServiceUpdate
from app.services.demo_rules import (
    cap_host,
    global_cap_reached,
    normalized_url,
    per_host_cap_reached,
)
from app.services.url_safety import ensure_registration_allowed

SEEDED_MESSAGE = "Seeded demo services cannot be changed."
ORDINARY_MESSAGE = "This service cannot be changed while demo mode is on."
# Transaction-level advisory lock that serializes the demo cap step across processes (D4).
_CAP_LOCK_KEY = zlib.crc32(b"service-monitoring-api:demo-caps")
_cap_thread_lock = threading.Lock()  # for databases without advisory locks (SQLite tests)


class ServiceNotFound(Exception):
    """404: no service with this id."""


class DuplicateUrl(Exception):
    """409: another service already has this normalized URL (RF-14)."""


class ServiceProtected(Exception):
    """403: seeded and ordinary services can't be changed in demo mode (RF-10, RF-11)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DemoIntervalTooLow(Exception):
    """422: check interval below the demo minimum (RF-13)."""

    def __init__(self, value: int, minimum: int) -> None:
        super().__init__(f"In demo mode the check interval must be at least {minimum} seconds")
        self.value = value
        self.message = str(self)


class DemoCapReached(Exception):
    """409: a demo cap is full (RF-15)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@contextmanager
def cap_guard(db: Session, active: bool) -> Iterator[None]:
    """Serialize the cap re-check and the commit (RF-15). Rolls back on any error."""
    if not active:
        yield
        return
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CAP_LOCK_KEY})
        try:
            yield
        except BaseException:
            db.rollback()  # also releases the transaction-level lock
            raise
        return
    with _cap_thread_lock:
        try:
            yield
        except BaseException:
            db.rollback()
            raise


def _visitor_urls(db: Session, exclude_id: int | None = None) -> list[str]:
    query = db.query(Service.url).filter(Service.origin == ServiceOrigin.VISITOR.value)
    if exclude_id is not None:
        query = query.filter(Service.id != exclude_id)
    return [url for (url,) in query]


def _check_global_cap(db: Session, demo: DemoConfig) -> None:
    count = db.query(Service.id).filter(Service.origin == ServiceOrigin.VISITOR.value).count()
    if global_cap_reached(count, demo.global_cap):
        raise DemoCapReached(
            f"Demo limit reached: at most {demo.global_cap} visitor services at a time."
        )


def _check_host_cap(
    db: Session, demo: DemoConfig, host: str | None, exclude_id: int | None
) -> None:
    if host is not None and per_host_cap_reached(
        _visitor_urls(db, exclude_id), host, demo.per_host_cap
    ):
        raise DemoCapReached(
            f"Demo limit reached: at most {demo.per_host_cap} visitor services for host {host}."
        )


def _check_create_caps(db: Session, demo: DemoConfig, host: str | None) -> None:
    _check_global_cap(db, demo)
    # Spec 003b inserts its per-client cap check here (RF-16 order).
    _check_host_cap(db, demo, host, exclude_id=None)


def duplicate_exists(db: Session, url: str, exclude_id: int | None) -> bool:
    """Layer 1 of RF-14: is there another service with this normalized URL?

    The exact query uses the unique index. Rows written outside the API may store another
    form of the same URL, so only when that finds nothing are stored URLs parsed again.
    Unparsable stored URLs never match.
    """
    exact = db.query(Service.id).filter(Service.url == url)
    if exclude_id is not None:
        exact = exact.filter(Service.id != exclude_id)
    if exact.first() is not None:
        return True
    target = normalized_url(url)
    for service_id, stored in db.query(Service.id, Service.url):
        if service_id != exclude_id and stored != url and normalized_url(stored) == target:
            return True
    return False


def _commit_or_duplicate(db: Session, url: str | None, exclude_id: int | None) -> None:
    """Commit; a uniqueness race on the URL becomes DuplicateUrl (layer 2 of RF-14)."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if url is not None and duplicate_exists(db, url, exclude_id):
            raise DuplicateUrl from None
        raise  # any other database error is not reported as a duplicate


def get_service_or_404(db: Session, service_id: int) -> Service:
    service = db.query(Service).filter(Service.id == service_id).first()
    if service is None:
        raise ServiceNotFound
    return service


def create_service(db: Session, payload: ServiceCreate) -> Service:
    demo = demo_settings.demo_config
    url = str(payload.url)
    data: dict[str, Any] = payload.model_dump()
    data["url"] = url
    host = cap_host(url)
    if demo is not None:
        if "check_interval" not in payload.model_fields_set:
            data["check_interval"] = demo.min_interval  # RF-12
        elif payload.check_interval < demo.min_interval:
            raise DemoIntervalTooLow(payload.check_interval, demo.min_interval)  # RF-13
        _check_create_caps(db, demo, host)  # before any DNS lookup (RF-16)
        data["origin"] = ServiceOrigin.VISITOR.value
    # Spec 002: an unsafe URL is a 422 even if it already exists, so before the duplicate.
    ensure_registration_allowed(payload.url.host, payload.submitted_url)
    if duplicate_exists(db, url, exclude_id=None):
        raise DuplicateUrl
    with cap_guard(db, active=demo is not None):
        if demo is not None:
            _check_create_caps(db, demo, host)  # atomic re-check (RF-15)
        service = Service(**data)
        db.add(service)
        _commit_or_duplicate(db, url, exclude_id=None)
    db.refresh(service)
    return service


def _ensure_changeable(service: Service) -> None:
    if service.origin == ServiceOrigin.SEEDED.value:
        raise ServiceProtected(SEEDED_MESSAGE)
    if service.origin == ServiceOrigin.ORDINARY.value:
        raise ServiceProtected(ORDINARY_MESSAGE)


def update_service(db: Session, service_id: int, payload: ServiceUpdate) -> Service:
    demo = demo_settings.demo_config
    service = get_service_or_404(db, service_id)
    updates: dict[str, Any] = payload.model_dump(exclude_unset=True)
    if demo is not None:
        _ensure_changeable(service)  # 403, even for an empty body (RF-10, RF-11)
        interval = payload.check_interval
        if "check_interval" in updates and interval is not None and interval < demo.min_interval:
            raise DemoIntervalTooLow(interval, demo.min_interval)  # RF-13, even if unchanged
    new_url: str | None = None
    if "url" in updates and payload.url is not None:
        new_url = str(payload.url)
        updates["url"] = new_url
    new_host = cap_host(new_url) if new_url is not None else None
    host_changes = demo is not None and new_url is not None and new_host != cap_host(service.url)
    if demo is not None and host_changes:
        _check_host_cap(db, demo, new_host, exclude_id=service.id)  # before DNS (RF-16)
    if new_url is not None and payload.url is not None:
        # Validated even when equal to the stored URL (spec 002).
        ensure_registration_allowed(payload.url.host, payload.submitted_url)
        if duplicate_exists(db, new_url, exclude_id=service.id):
            raise DuplicateUrl
    with cap_guard(db, active=host_changes):
        if demo is not None and host_changes:
            _check_host_cap(db, demo, new_host, exclude_id=service.id)  # atomic re-check
        for field_name, value in updates.items():
            setattr(service, field_name, value)
        _commit_or_duplicate(db, new_url, exclude_id=service.id)
    db.refresh(service)
    return service


def delete_service(db: Session, service_id: int) -> None:
    service = get_service_or_404(db, service_id)
    if demo_settings.demo_config is not None:
        _ensure_changeable(service)  # RF-10, RF-11
    db.delete(service)
    db.commit()
