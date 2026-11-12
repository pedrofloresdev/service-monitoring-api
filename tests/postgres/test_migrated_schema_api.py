"""
Spec 001 RF-2 / RF-9: on a database built from the migration history, a check stored without
an explicit timestamp gets one, and the metrics and status endpoints work with it.
"""

from collections.abc import Callable, Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.metrics import Metric
from app.db.models.services import Service
from app.db.session import get_db
from app.main import app
from tests.postgres.conftest import AlembicRunner

pytestmark = pytest.mark.postgres

TARGET_REVISION = "head"


@pytest.fixture
def migrated_session(make_database: Callable[[], str], alembic: AlembicRunner) -> Iterator[Session]:
    url = make_database()
    result = alembic(url, "upgrade", TARGET_REVISION)
    assert result.returncode == 0, result.output
    engine = create_engine(url)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def pg_client(migrated_session: Session) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        yield migrated_session

    app.dependency_overrides[get_db] = override_get_db
    with (
        patch("app.main.start_scheduler"),
        patch("app.main.stop_scheduler"),
        patch("app.api.v1.services.schedule_service"),
        patch("app.api.v1.services.unschedule_service"),
    ):
        with TestClient(app) as client:
            yield client
    app.dependency_overrides.clear()


def test_check_without_timestamp_gets_one(migrated_session: Session) -> None:
    service = Service(name="pg", url="https://pg.example/health")
    migrated_session.add(service)
    migrated_session.commit()

    metric = Metric(service_id=service.id, status="UP", status_code=200, response_time_ms=12.5)
    migrated_session.add(metric)
    migrated_session.commit()
    migrated_session.refresh(metric)

    assert metric.checked_at is not None


def test_metrics_and_status_endpoints_on_migrated_schema(
    pg_client: TestClient, migrated_session: Session
) -> None:
    service = Service(name="pg", url="https://pg.example/health")
    migrated_session.add(service)
    migrated_session.commit()
    # Stored the way the scheduler stores checks: no explicit checked_at.
    migrated_session.add(
        Metric(service_id=service.id, status="UP", status_code=200, response_time_ms=12.5)
    )
    migrated_session.commit()

    metrics = pg_client.get(f"/api/v1/metrics/services/{service.id}")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["total"] == 1
    assert body["items"][0]["checked_at"] is not None

    status = pg_client.get("/api/v1/status").json()
    assert status["services"][0]["uptime_last_24h"] > 0
