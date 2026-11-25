"""Seed validation and seeding at startup (spec 003a RF-4 to RF-9)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session, sessionmaker

from app.core.demo_settings import DemoConfig, DemoConfigError
from app.db.models.services import Service, ServiceOrigin
from app.db.session import SessionLocal
from app.services.demo_rules import (
    SEED_VARIABLE,
    SeedDefinition,
    StoredService,
    parse_url,
    plan_seeding,
)
from app.services.url_safety import UnsafeDestinationError, ensure_registration_allowed

logger = logging.getLogger(__name__)


def _is_unsafe(seed: SeedDefinition) -> bool:
    url = parse_url(seed.url)
    if url is None:  # already rejected by load_demo_config
        return False
    try:
        ensure_registration_allowed(url.host, seed.url)
    except UnsafeDestinationError:
        return True
    return False


def validate_seed_urls(config: DemoConfig) -> None:
    """Spec 002 check of every seed, all hostnames resolved at the same time (RF-4).

    The error names the definition by position and name, never the resolved addresses.
    """
    if not config.seeds:
        return
    with ThreadPoolExecutor(max_workers=len(config.seeds)) as pool:
        unsafe = list(pool.map(_is_unsafe, config.seeds))
    errors = [
        f"{SEED_VARIABLE}: definition #{position} ({seed.name}): "
        "url resolves to a non-public address"
        for position, (seed, bad) in enumerate(zip(config.seeds, unsafe, strict=True), start=1)
        if bad
    ]
    if errors:
        raise DemoConfigError(errors)


def _apply_fields(service: Service, seed: SeedDefinition) -> None:
    service.name = seed.name
    service.expected_status = seed.expected_status
    service.check_interval = seed.check_interval
    service.is_active = True
    service.origin = ServiceOrigin.SEEDED.value


def apply_seed_plan(db: Session, config: DemoConfig) -> list[int]:
    """Create, adopt and demote in the caller's transaction, without committing (RF-5, RF-6).

    Metrics are never touched (RF-9). Returns the ids of every seeded service.
    """
    rows = {service.id: service for service in db.query(Service)}
    plan = plan_seeding([StoredService(s.id, s.url, s.origin) for s in rows.values()], config.seeds)
    for warning in plan.warnings:
        logger.warning("Demo seeding: %s", warning)
    created = []
    for seed in plan.creates:
        service = Service(url=seed.url)
        _apply_fields(service, seed)
        db.add(service)
        created.append(service)
    for service_id, seed in plan.adoptions:
        _apply_fields(rows[service_id], seed)
    for service_id in plan.demotions:
        rows[service_id].origin = ServiceOrigin.ORDINARY.value
    db.flush()
    logger.info(
        "Demo seeding: %d created, %d adopted, %d demoted",
        len(plan.creates),
        len(plan.adoptions),
        len(plan.demotions),
    )
    return sorted([s.id for s in created] + [service_id for service_id, _ in plan.adoptions])


def seed_at_startup(
    config: DemoConfig, session_factory: sessionmaker[Session] = SessionLocal
) -> list[int]:
    """Apply the seed plan in one transaction; any failure commits nothing (RF-5, RF-7)."""
    with session_factory() as db, db.begin():
        return apply_seed_plan(db, config)
