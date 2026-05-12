import os

# Must be set before any app module is imported so pydantic-settings picks it up.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from unittest.mock import patch  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.db.models.services import Service  # noqa: E402
from app.db.models.metrics import Metric  # noqa: E402


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session():
    """Fresh in-memory SQLite database per test — no shared state."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# HTTP client fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db_session):
    """TestClient wired to the in-memory DB with scheduler ops mocked out."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch("app.main.start_scheduler"),
        patch("app.main.stop_scheduler"),
        patch("app.api.v1.services.schedule_service"),
        patch("app.api.v1.services.unschedule_service"),
    ):
        with TestClient(app) as test_client:
            yield test_client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def service(db_session) -> Service:
    svc = Service(
        name="Test API",
        url="https://example.com/health",
        expected_status=200,
        check_interval=60,
        is_active=True,
    )
    db_session.add(svc)
    db_session.commit()
    db_session.refresh(svc)
    return svc


def make_metric(db_session, service_id: int, status: str, response_time_ms: float = 100.0) -> Metric:
    metric = Metric(
        service_id=service_id,
        status=status,
        status_code=200 if status == "UP" else 503,
        response_time_ms=response_time_ms,
        checked_at=datetime.now(timezone.utc),
    )
    db_session.add(metric)
    db_session.commit()
    db_session.refresh(metric)
    return metric
