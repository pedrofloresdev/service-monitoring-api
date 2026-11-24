"""Demo-mode startup (spec 003a RF-4 to RF-9): seed validation, seeding, immediate first checks."""

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import config as config_module
from app.core import demo_settings
from app.core.demo_settings import DemoConfig, DemoConfigError
from app.db.base import Base
from app.db.models.metrics import Metric
from app.db.models.services import Service
from app.services import demo_seeding
from app.services.demo_rules import SeedDefinition
from app.workers import scheduler as scheduler_module
from tests.conftest import FakeResolver


def _config(*seeds: SeedDefinition) -> DemoConfig:
    return DemoConfig(seeds=seeds, global_cap=20, per_host_cap=3, min_interval=300)


def _seed(url: str, name: str = "Seed", interval: int = 60) -> SeedDefinition:
    return SeedDefinition(name, url, 200, interval)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


def _rows(factory: sessionmaker[Session]) -> list[tuple[str, str, int, bool]]:
    with factory() as db:
        return [
            (s.url, s.origin, s.check_interval, s.is_active)
            for s in db.query(Service).order_by(Service.id)
        ]


# ---------------------------------------------------------------------------
# RF-4: seed URLs and spec 002
# ---------------------------------------------------------------------------


class TestValidateSeedUrls:
    def test_public_and_unresolvable_hostnames_are_accepted(
        self, fake_resolver: FakeResolver
    ) -> None:
        fake_resolver.set("gone.example", error="name not found")
        demo_seeding.validate_seed_urls(
            _config(_seed("https://ok.example/"), _seed("https://gone.example/"))
        )

    def test_hostname_resolving_to_non_public_refuses(self, fake_resolver: FakeResolver) -> None:
        fake_resolver.set("internal.example", "10.0.0.5")
        config = _config(
            _seed("https://ok.example/"), _seed("https://internal.example/", name="Inside")
        )
        with pytest.raises(DemoConfigError) as exc:
            demo_seeding.validate_seed_urls(config)
        assert exc.value.errors == [
            "DEMO_SEED_SERVICES: definition #2 (Inside): url resolves to a non-public address"
        ]
        assert "10.0.0.5" not in str(exc.value)

    def test_hostnames_are_resolved_concurrently(self, fake_resolver: FakeResolver) -> None:
        fake_resolver.delay = 0.5
        config = _config(*[_seed(f"https://h{i}.example/") for i in range(4)])
        started = time.monotonic()
        demo_seeding.validate_seed_urls(config)
        assert time.monotonic() - started < 1.5  # sequential would take about 2 s


# ---------------------------------------------------------------------------
# RF-5, RF-6, RF-7, RF-9: seeding at startup
# ---------------------------------------------------------------------------


class TestSeedAtStartup:
    def test_seeds_are_created(self, session_factory: sessionmaker[Session]) -> None:
        ids = demo_seeding.seed_at_startup(_config(_seed("https://a.example/")), session_factory)
        assert _rows(session_factory) == [("https://a.example/", "seeded", 60, True)]
        assert len(ids) == 1

    def test_restart_creates_no_duplicate_and_keeps_checks(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        config = _config(_seed("https://a.example/"))
        [first_id] = demo_seeding.seed_at_startup(config, session_factory)
        with session_factory() as db:
            db.add(Metric(service_id=first_id, status="UP", status_code=200, response_time_ms=10.0))
            db.commit()
        assert demo_seeding.seed_at_startup(config, session_factory) == [first_id]
        with session_factory() as db:
            assert db.query(Service).count() == 1 and db.query(Metric).count() == 1

    def test_adopts_corrects_reactivates_and_demotes(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory() as db:
            db.add_all(
                [
                    Service(
                        name="v",
                        url="https://A.example",
                        check_interval=999,
                        is_active=False,
                        origin="visitor",
                    ),
                    Service(name="old", url="https://old.example/", origin="seeded"),
                    Service(name="o", url="https://ordinary.example/", origin="ordinary"),
                ]
            )
            db.commit()
        demo_seeding.seed_at_startup(
            _config(_seed("https://a.example/", interval=120)), session_factory
        )
        assert _rows(session_factory) == [
            ("https://A.example", "seeded", 120, True),
            ("https://old.example/", "ordinary", 60, True),
            ("https://ordinary.example/", "ordinary", 60, True),
        ]

    def test_failure_refuses_startup_and_commits_nothing(
        self, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("database unavailable")

        real_apply = demo_seeding.apply_seed_plan

        def apply_then_fail(db: Session, config: DemoConfig) -> list[int]:
            real_apply(db, config)
            boom()
            return []

        monkeypatch.setattr(demo_seeding, "apply_seed_plan", apply_then_fail)
        with pytest.raises(RuntimeError):
            demo_seeding.seed_at_startup(_config(_seed("https://a.example/")), session_factory)
        assert _rows(session_factory) == []


# ---------------------------------------------------------------------------
# RF-8: immediate first checks, exempt from the lateness cutoff (real scheduler)
# ---------------------------------------------------------------------------


class TestFirstChecks:
    def test_twenty_slow_first_checks_with_ten_workers_none_skipped(
        self, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ids = demo_seeding.seed_at_startup(
            _config(*[_seed(f"https://s{i}.example/", interval=3600) for i in range(20)]),
            session_factory,
        )
        done: list[int] = []
        lock = threading.Lock()

        def slow_check(service_id: int) -> None:
            time.sleep(1.2)  # longer than the default 1 s misfire grace
            with lock:
                done.append(service_id)

        real = BackgroundScheduler()  # default: 10 worker threads, misfire grace 1 s
        monkeypatch.setattr(scheduler_module, "scheduler", real)
        monkeypatch.setattr(scheduler_module, "SessionLocal", session_factory)
        monkeypatch.setattr(scheduler_module, "check_service_by_id", slow_check)
        try:
            scheduler_module.start_scheduler(first_check_ids=ids)
            interval_job = real.get_job(f"service_{ids[0]}")
            assert interval_job is not None and interval_job.misfire_grace_time == 1
            deadline = time.monotonic() + 10
            while len(done) < 20 and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            real.shutdown(wait=False)
        assert sorted(done) == sorted(ids)

    def test_first_check_job_has_no_lateness_cutoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real = BackgroundScheduler()
        monkeypatch.setattr(scheduler_module, "scheduler", real)
        scheduler_module.schedule_first_check(42)
        job = real.get_job("first_check_42")
        assert job is not None and job.misfire_grace_time is None


# ---------------------------------------------------------------------------
# Startup wiring (lifespan)
# ---------------------------------------------------------------------------


class TestLifespan:
    def _run(self, monkeypatch: pytest.MonkeyPatch, seed_result: Callable[..., list[int]]) -> Any:
        from app import main

        started: dict[str, Any] = {}
        monkeypatch.setattr(config_module.settings, "DEMO_MODE", "true")
        monkeypatch.setattr(main, "load_demo_config", lambda: _config(_seed("https://a.example/")))
        monkeypatch.setattr(main, "validate_seed_urls", lambda config: None)
        monkeypatch.setattr(main, "seed_at_startup", seed_result)
        monkeypatch.setattr(main, "start_scheduler", lambda **kw: started.update(kw))
        monkeypatch.setattr(main, "stop_scheduler", lambda: None)
        monkeypatch.setattr(demo_settings, "demo_config", None)
        with TestClient(main.app):
            assert demo_settings.demo_config is not None
        return started

    def test_demo_startup_seeds_then_starts_scheduler_with_first_checks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started = self._run(monkeypatch, lambda config: [7, 9])
        assert started == {"first_check_ids": [7, 9]}

    def test_seeding_failure_refuses_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail(config: DemoConfig) -> list[int]:
            raise RuntimeError("seeding failed")

        with pytest.raises(RuntimeError, match="seeding failed"):
            self._run(monkeypatch, fail)
