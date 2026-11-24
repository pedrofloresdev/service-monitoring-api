import os

# Must be set before any app module is imported so pydantic-settings picks it up.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import ipaddress  # noqa: E402
import socket  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Any  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session as OrmSession  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.models.metrics import Metric  # noqa: E402
from app.db.models.services import Service  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import url_safety  # noqa: E402
from app.services.url_safety import IPAddress, Resolution  # noqa: E402

# ---------------------------------------------------------------------------
# Network guard (spec 002 RNF-7)
# ---------------------------------------------------------------------------


class NetworkAccessBlocked(RuntimeError):
    """Raised when a default-suite test tries to resolve a name or reach a non-loopback host.

    A RuntimeError (not an OSError) so library code that handles network errors cannot
    swallow it and make a test pass for the wrong reason.
    """


def _is_loopback_literal(host: object) -> bool:
    if not isinstance(host, str):
        return False
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def network_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow only loopback literals; PostgreSQL tests (deselected by default) are exempt."""
    if request.node.get_closest_marker("postgres"):
        return

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and not _is_loopback_literal(host):
            raise NetworkAccessBlocked(f"DNS lookup of {host!r} blocked in tests")
        return real_getaddrinfo(host, *args, **kwargs)

    def check_address(address: Any) -> None:
        if isinstance(address, tuple) and not _is_loopback_literal(address[0]):
            raise NetworkAccessBlocked(f"connection to {address[0]!r} blocked in tests")

    def guarded_connect(self: socket.socket, address: Any) -> None:
        check_address(address)
        real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        check_address(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


# ---------------------------------------------------------------------------
# Fake resolver (spec 002): every hostname resolves to a public address unless a test
# says otherwise, so the code under test never needs real DNS.
# ---------------------------------------------------------------------------

PUBLIC_TEST_ADDRESS = "93.184.216.34"


@dataclass
class FakeResolver:
    answers: dict[str, Resolution] = field(default_factory=dict)
    delay: float = 0.0
    calls: list[str] = field(default_factory=list)

    def set(self, host: str, *addresses: str, error: str | None = None) -> None:
        parsed: tuple[IPAddress, ...] = tuple(ipaddress.ip_address(a) for a in addresses)
        self.answers[host] = Resolution(parsed, None if parsed else (error or "name not found"))

    def __call__(self, host: str, timeout: float, getaddrinfo: Any = None) -> Resolution:
        self.calls.append(host)
        if self.delay:
            time.sleep(self.delay)
        default = Resolution((ipaddress.ip_address(PUBLIC_TEST_ADDRESS),), None)
        return self.answers.get(host, default)


@pytest.fixture(autouse=True)
def fake_resolver(monkeypatch: pytest.MonkeyPatch) -> FakeResolver:
    resolver = FakeResolver()
    monkeypatch.setattr(url_safety, "resolve_host", resolver)
    return resolver


# Captured at import, before the autouse fake_resolver replaces it.
REAL_RESOLVE_HOST = url_safety.resolve_host

PartialDns = Callable[[int, str], None]


@pytest.fixture
def partial_dns(monkeypatch: pytest.MonkeyPatch) -> PartialDns:
    """Run the REAL resolver over a fake getaddrinfo where one family answers and the
    other fails (spec 002 RNF-6 partial answers). Call it as partial_dns(family, address)."""
    monkeypatch.setattr(url_safety, "resolve_host", REAL_RESOLVE_HOST)

    def install(answered_family: int, address: str) -> None:
        def fake_getaddrinfo(host: Any, port: Any, family: int, *args: Any) -> Any:
            if family == answered_family:
                sockaddr = (address, 0) if family == socket.AF_INET else (address, 0, 0, 0)
                return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    return install


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session() -> Iterator[OrmSession]:
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
def client(db_session: OrmSession) -> Iterator[TestClient]:
    """TestClient wired to the in-memory DB with scheduler ops mocked out."""

    def override_get_db() -> Iterator[OrmSession]:
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
def service(db_session: OrmSession) -> Service:
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


def make_metric(
    db_session: OrmSession, service_id: int, status: str, response_time_ms: float = 100.0
) -> Metric:
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


# ---------------------------------------------------------------------------
# Demo mode (spec 003a): switch it on for one test
# ---------------------------------------------------------------------------


@pytest.fixture
def demo_config() -> Iterator[Any]:
    """Demo mode on for one test, with small caps; restored afterwards."""
    from app.core import demo_settings
    from app.services.demo_rules import SeedDefinition

    config = demo_settings.DemoConfig(
        seeds=(SeedDefinition("Seed", "https://seed.example/", 200, 60),),
        global_cap=4,
        per_host_cap=2,
        min_interval=300,
    )
    previous = demo_settings.demo_config
    demo_settings.demo_config = config
    try:
        yield config
    finally:
        demo_settings.demo_config = previous
