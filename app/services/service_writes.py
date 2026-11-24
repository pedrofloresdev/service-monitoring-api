"""Create, update and delete flows for services (spec 003a; spec 002 checks kept).

Every check runs before anything is changed, in the order of spec 003a RF-16. A rejected
write leaves the service untouched. The routes schedule or unschedule only after these
functions return, i.e. after a successful commit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.services import Service
from app.schemas.services import ServiceCreate, ServiceUpdate
from app.services.demo_rules import normalized_url
from app.services.url_safety import ensure_registration_allowed


class ServiceNotFound(Exception):
    """404: no service with this id."""


class DuplicateUrl(Exception):
    """409: another service already has this normalized URL (RF-14)."""


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
    # Spec 002: an unsafe URL is a 422 even if it already exists, so before the duplicate.
    ensure_registration_allowed(payload.url.host, payload.submitted_url)
    url = str(payload.url)
    if duplicate_exists(db, url, exclude_id=None):
        raise DuplicateUrl
    data: dict[str, Any] = payload.model_dump()
    data["url"] = url
    service = Service(**data)
    db.add(service)
    _commit_or_duplicate(db, url, exclude_id=None)
    db.refresh(service)
    return service


def update_service(db: Session, service_id: int, payload: ServiceUpdate) -> Service:
    service = get_service_or_404(db, service_id)
    updates: dict[str, Any] = payload.model_dump(exclude_unset=True)
    new_url: str | None = None
    if "url" in updates and payload.url is not None:
        # Validated even when equal to the stored URL (spec 002).
        ensure_registration_allowed(payload.url.host, payload.submitted_url)
        new_url = str(payload.url)
        updates["url"] = new_url
        if duplicate_exists(db, new_url, exclude_id=service.id):
            raise DuplicateUrl
    for field_name, value in updates.items():
        setattr(service, field_name, value)
    _commit_or_duplicate(db, new_url, exclude_id=service.id)
    db.refresh(service)
    return service


def delete_service(db: Session, service_id: int) -> None:
    service = get_service_or_404(db, service_id)
    db.delete(service)
    db.commit()
