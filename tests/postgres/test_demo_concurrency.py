"""Spec 003a RNF-3: caps and duplicate detection hold under concurrent writes on PostgreSQL.

Each thread uses its own session, as concurrent requests do. A barrier releases all of
them at once so they race through the checks and the commit.
"""

import threading
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.demo_settings import DemoConfig
from app.db.models.services import Service, ServiceOrigin
from app.schemas.services import ServiceCreate, ServiceUpdate
from app.services import service_writes
from app.services.demo_rules import cap_host
from tests.postgres.conftest import AlembicRunner

pytestmark = pytest.mark.postgres

THREADS = 8
Outcome = str  # "ok", or the name of the domain exception raised


@pytest.fixture
def factory(
    make_database: Callable[[], str], alembic: AlembicRunner
) -> Iterator[sessionmaker[Session]]:
    url = make_database()
    result = alembic(url, "upgrade", "head")
    assert result.returncode == 0, result.output
    engine = create_engine(url, pool_size=THREADS, max_overflow=THREADS)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


def _insert(factory: sessionmaker[Session], *urls: str, origin: str = "visitor") -> list[int]:
    with factory() as db:
        services = [Service(name=f"s{i}", url=u, origin=origin) for i, u in enumerate(urls)]
        db.add_all(services)
        db.commit()
        return [s.id for s in services]


def _race(factory: sessionmaker[Session], work: list[Callable[[Session], object]]) -> list[Outcome]:
    barrier = threading.Barrier(len(work))
    outcomes: list[Outcome] = [""] * len(work)

    def run(index: int) -> None:
        with factory() as db:
            barrier.wait()
            try:
                work[index](db)
                outcomes[index] = "ok"
            except Exception as exc:  # anything unexpected is reported by name, never hidden
                outcomes[index] = type(exc).__name__

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(work))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return outcomes


def _visitor_urls(factory: sessionmaker[Session]) -> list[str]:
    with factory() as db:
        return [
            url
            for (url,) in db.query(Service.url).filter(
                Service.origin == ServiceOrigin.VISITOR.value
            )
        ]


def _create(url: str) -> Callable[[Session], object]:
    return lambda db: service_writes.create_service(
        db, ServiceCreate(name="n", url=url, check_interval=300)
    )


def _update(service_id: int, url: str) -> Callable[[Session], object]:
    return lambda db: service_writes.update_service(db, service_id, ServiceUpdate(url=url))


def test_concurrent_creates_at_global_cap_never_exceed_it(
    factory: sessionmaker[Session], demo_config: DemoConfig
) -> None:
    _insert(factory, *[f"https://pre{i}.example/" for i in range(demo_config.global_cap - 1)])
    outcomes = _race(factory, [_create(f"https://new{i}.example/") for i in range(THREADS)])
    assert sorted(outcomes) == ["DemoCapReached"] * (THREADS - 1) + ["ok"]
    assert len(_visitor_urls(factory)) == demo_config.global_cap


def test_concurrent_creates_at_per_host_cap_never_exceed_it(
    factory: sessionmaker[Session], demo_config: DemoConfig
) -> None:
    _insert(factory, *[f"https://h.example/pre{i}" for i in range(demo_config.per_host_cap - 1)])
    outcomes = _race(factory, [_create(f"https://h.example/new{i}") for i in range(THREADS)])
    assert sorted(outcomes) == ["DemoCapReached"] * (THREADS - 1) + ["ok"]
    hosts = [cap_host(u) for u in _visitor_urls(factory)]
    assert hosts.count("h.example") == demo_config.per_host_cap


def test_concurrent_host_changing_updates_never_exceed_per_host_cap(
    factory: sessionmaker[Session], demo_config: DemoConfig
) -> None:
    _insert(factory, *[f"https://t.example/pre{i}" for i in range(demo_config.per_host_cap - 1)])
    ids = _insert(factory, *[f"https://other{i}.example/" for i in range(THREADS)])
    outcomes = _race(factory, [_update(i, f"https://t.example/moved{i}") for i in ids])
    assert sorted(outcomes) == ["DemoCapReached"] * (THREADS - 1) + ["ok"]
    hosts = [cap_host(u) for u in _visitor_urls(factory)]
    assert hosts.count("t.example") == demo_config.per_host_cap


@pytest.mark.parametrize("demo", [False, True], ids=["demo-off", "demo-on"])
def test_concurrent_duplicate_creates_give_409_never_500(
    factory: sessionmaker[Session], request: pytest.FixtureRequest, demo: bool
) -> None:
    if demo:
        request.getfixturevalue("demo_config")
    outcomes = _race(factory, [_create("https://same.example/") for _ in range(THREADS)])
    assert sorted(outcomes) == ["DuplicateUrl"] * (THREADS - 1) + ["ok"]


@pytest.mark.parametrize("demo", [False, True], ids=["demo-off", "demo-on"])
def test_concurrent_duplicate_updates_give_409_never_500(
    factory: sessionmaker[Session], request: pytest.FixtureRequest, demo: bool
) -> None:
    if demo:
        request.getfixturevalue("demo_config")
    ids = _insert(factory, *[f"https://u{i}.example/" for i in range(THREADS)])
    outcomes = _race(factory, [_update(i, "https://same.example/") for i in ids])
    assert sorted(outcomes) == ["DuplicateUrl"] * (THREADS - 1) + ["ok"]
