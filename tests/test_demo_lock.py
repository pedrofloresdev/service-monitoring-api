"""The demo lock with a fake connection (spec 003d RF-5, RF-7, RF-7a; RNF-3, RNF-6).

Time-based behaviour uses an injected clock and sleep, or a 1-second timeout, never the
default settings.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg2
import pytest

from app.core.demo_settings import LockConfig
from app.services import demo_lock
from app.services.demo_lock import DemoLock, LockRefused
from app.services.demo_lock_rules import connect_kwargs, lock_key

URL = "postgresql://demo:s3cret@db:5432/app"
KEY = lock_key("demo", "app")


def _config(wait: int = 3, heartbeat: int = 1, timeout: int = 1) -> LockConfig:
    return LockConfig(wait=wait, heartbeat=heartbeat, timeout=timeout, keepalive=30, name="demo")


@dataclass
class FakeConnection:
    """Answers the try-lock and held queries; can raise or hang on demand."""

    try_lock: list[bool] = field(default_factory=lambda: [True])
    held: Any = True  # True, False, an exception instance, or "hang"
    queries: list[tuple[str, dict[str, int]]] = field(default_factory=list)
    query_started: list[float] = field(default_factory=list)
    closed: bool = False
    autocommit: bool = False
    release_hang: threading.Event = field(default_factory=threading.Event)

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeCursor:
    conn: FakeConnection
    result: Any = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, int]) -> None:
        self.conn.queries.append((sql, params))
        self.conn.query_started.append(time.monotonic())
        if "pg_try_advisory_lock" in sql:
            answers = self.conn.try_lock
            self.result = answers.pop(0) if len(answers) > 1 else answers[0]
            return
        if self.conn.held == "hang":
            self.conn.release_hang.wait(30)
        if isinstance(self.conn.held, BaseException):
            raise self.conn.held
        self.result = self.conn.held

    def fetchone(self) -> tuple[Any]:
        return (self.result,)


class FakeClock:
    """Time moves only when the code sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float, stop: threading.Event) -> bool:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds
        return stop.is_set()


class Connector:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, BaseException):
            raise result
        return result


def _acquire(connector: Connector, clock: FakeClock, config: LockConfig) -> DemoLock | None:
    return DemoLock.acquire(
        config,
        URL,
        stop=threading.Event(),
        connect=connector,
        clock=clock,
        sleep=clock.sleep,
        exit_fn=lambda code: None,
    )


REFUSED = psycopg2.OperationalError(
    'connection to server at "db" (10.0.0.2), port 5432 failed: Connection refused'
)

# ---------------------------------------------------------------------------
# RF-5: taking the lock
# ---------------------------------------------------------------------------


class TestAcquire:
    def test_free_lock_is_taken_on_the_first_attempt(self) -> None:
        conn = FakeConnection()
        clock = FakeClock()
        connector = Connector(conn)
        lock = _acquire(connector, clock, _config())
        assert lock is not None and conn.autocommit is True
        assert conn.queries == [(demo_lock.TRY_LOCK_SQL, {"key": KEY})]
        assert clock.sleeps == []

    def test_connector_gets_the_lock_connection_options_not_the_engine(self) -> None:
        connector = Connector(FakeConnection())
        _acquire(connector, FakeClock(), _config())
        assert connector.calls == [connect_kwargs(URL, keepalive_k=30)]  # RNF-3: psycopg2 only

    def test_held_lock_is_retried_every_second_until_the_wait_ends(self) -> None:
        conn = FakeConnection(try_lock=[False])
        clock = FakeClock()
        with pytest.raises(LockRefused) as exc:
            _acquire(Connector(conn), clock, _config(wait=3))
        assert exc.value.message == (
            "Demo mode requires a single process: another process holds the demo lock"
        )
        assert clock.sleeps == [1.0, 1.0, 1.0]
        assert len(conn.queries) == 4  # attempts at 0, 1, 2 and 3 s
        assert conn.closed

    def test_wait_zero_is_exactly_one_attempt(self) -> None:
        conn = FakeConnection(try_lock=[False])
        clock = FakeClock()
        with pytest.raises(LockRefused):
            _acquire(Connector(conn), clock, _config(wait=0))
        assert len(conn.queries) == 1 and clock.sleeps == []

    def test_lock_freed_during_the_wait_is_taken(self) -> None:
        conn = FakeConnection(try_lock=[False, False, True])
        clock = FakeClock()
        assert _acquire(Connector(conn), clock, _config(wait=60)) is not None
        assert clock.sleeps == [1.0, 1.0]

    def test_unreachable_database_is_retried_then_refused(self) -> None:
        clock = FakeClock()
        connector = Connector(REFUSED)
        with pytest.raises(LockRefused) as exc:
            _acquire(connector, clock, _config(wait=2))
        assert exc.value.message == "Demo mode could not reach the database to take the demo lock"
        assert len(connector.calls) == 3

    def test_database_coming_up_during_the_wait(self) -> None:
        clock = FakeClock()
        connector = Connector(REFUSED, REFUSED, FakeConnection())
        assert _acquire(connector, clock, _config(wait=60)) is not None
        assert len(connector.calls) == 3

    def test_reached_once_then_held_reports_the_other_process(self) -> None:
        clock = FakeClock()
        connector = Connector(FakeConnection(try_lock=[False]))
        with pytest.raises(LockRefused) as exc:
            _acquire(connector, clock, _config(wait=1))
        assert "another process holds the demo lock" in exc.value.message

    def test_broken_connection_is_replaced(self) -> None:
        broken = FakeConnection(try_lock=[False])
        broken.cursor = lambda: (_ for _ in ()).throw(psycopg2.OperationalError("gone"))  # type: ignore[method-assign]
        good = FakeConnection()
        connector = Connector(broken, good)
        assert _acquire(connector, FakeClock(), _config(wait=60)) is not None
        assert broken.closed and len(connector.calls) == 2

    @pytest.mark.parametrize(
        "message",
        [
            'connection to server at "db", port 5432 failed: FATAL:  password authentication'
            ' failed for user "demo" (password s3cret)',
            'connection to server at "db", port 5432 failed: FATAL:  database "app" does not exist',
        ],
    )
    def test_permanent_error_is_refused_at_once_without_the_password(self, message: str) -> None:
        clock = FakeClock()
        connector = Connector(psycopg2.OperationalError(message))
        with pytest.raises(LockRefused) as exc:
            _acquire(connector, clock, _config(wait=60))
        assert exc.value.message.startswith("Demo mode could not connect to the database: ")
        assert "s3cret" not in exc.value.message
        assert len(connector.calls) == 1 and clock.sleeps == []

    def test_stop_during_a_hanging_connect_returns_none_quickly(self) -> None:
        stop = threading.Event()
        hang = threading.Event()

        def connect(**kwargs: Any) -> Any:
            hang.wait(30)

        threading.Timer(0.1, stop.set).start()
        started = time.monotonic()
        result = DemoLock.acquire(_config(wait=60), URL, stop=stop, connect=connect)
        hang.set()
        assert result is None
        assert time.monotonic() - started < 0.3

    def test_stop_during_the_retry_wait_returns_none(self) -> None:
        stop = threading.Event()
        conn = FakeConnection(try_lock=[False])

        def sleep(seconds: float, event: threading.Event) -> bool:
            event.set()
            return True

        result = DemoLock.acquire(
            _config(wait=60), URL, stop=stop, connect=Connector(conn), sleep=sleep
        )
        assert result is None and conn.closed
