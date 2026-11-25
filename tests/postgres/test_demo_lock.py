"""Spec 003d RNF-4: the start command and the demo lock in real processes on PostgreSQL.

Each process runs `tests.postgres.lock_holder` (the real start command with a stand-in
server). Timing uses the shortest settings (heartbeat 2 s, timeout 1 s) and generous
margins, never the defaults (RNF-6).
"""

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from app.core.demo_settings import LockConfig
from app.services.demo_lock import DemoLock
from app.services.demo_lock_rules import APPLICATION_NAME, keepalive_settings

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED = '[{"name": "Example", "url": "https://example.com/"}]'


class Holder:
    """A start-command process whose output is collected line by line."""

    def __init__(self, database_url: str, **env: str) -> None:
        full_env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("DEMO_") and k != "WEB_CONCURRENCY"
        }
        full_env.update(
            {
                "DATABASE_URL": database_url,
                "DEMO_MODE": "true",
                "DEMO_SEED_SERVICES": SEED,
                "DEMO_LOCK_WAIT_SECONDS": "0",
                "DEMO_LOCK_HEARTBEAT_SECONDS": "2",
                "DEMO_LOCK_TIMEOUT_SECONDS": "1",
                "HOLDER_MIGRATION": "noop",
                "PYTHONUNBUFFERED": "1",
                **env,
            }
        )
        self.lines: list[str] = []
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tests.postgres.lock_holder"],
            cwd=REPO_ROOT,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.append(line.rstrip("\n"))

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def wait_for(self, text: str, timeout: float = 15) -> float:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(text in line for line in self.lines):
                return time.monotonic()
            if self.process.poll() is not None:
                time.sleep(0.2)  # let the reader catch up
                if any(text in line for line in self.lines):
                    return time.monotonic()
                break
            time.sleep(0.05)
        raise AssertionError(f"{text!r} not seen; output:\n{self.output}")

    def wait_exit(self, timeout: float) -> int:
        try:
            return self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"did not exit within {timeout} s; output:\n{self.output}"
            ) from None

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(5)


@pytest.fixture
def database(make_database: Callable[[], str]) -> str:
    return make_database()


@pytest.fixture
def holders() -> Iterator[Callable[..., Holder]]:
    started: list[Holder] = []

    def start(database_url: str, **env: str) -> Holder:
        holder = Holder(database_url, **env)
        started.append(holder)
        return holder

    yield start
    for holder in started:
        holder.stop()


def _lock_backend_pid(database_url: str) -> int:
    with psycopg2.connect(database_url) as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT pid FROM pg_stat_activity WHERE application_name = %s AND datname = %s",
            (APPLICATION_NAME, make_url(database_url).database),
        )
        rows = cursor.fetchall()
    assert len(rows) == 1, rows
    return int(rows[0][0])


def _terminate(database_url: str, pid: int) -> None:
    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_terminate_backend(%s)", (pid,))
    conn.close()


def _config(**overrides: int) -> LockConfig:
    values = {"wait": 0, "heartbeat": 2, "timeout": 1, "keepalive": 30, **overrides}
    return LockConfig(name="pytest-demo", **values)


# ---------------------------------------------------------------------------
# RF-5: waiting, refusing, taking over, stopping
# ---------------------------------------------------------------------------


def test_second_process_waits_then_refuses(database: str, holders: Callable[..., Holder]) -> None:
    first = holders(database)
    first.wait_for("READY")
    started = time.monotonic()
    second = holders(database, DEMO_LOCK_WAIT_SECONDS="2")
    assert second.wait_exit(10) == 1
    assert 1.8 <= time.monotonic() - started < 8
    assert "another process holds the demo lock" in second.output
    assert "Running migrations" not in second.output  # migrations only after the lock


def test_wait_zero_is_one_attempt(database: str, holders: Callable[..., Holder]) -> None:
    holders(database).wait_for("READY")
    started = time.monotonic()
    second = holders(database, DEMO_LOCK_WAIT_SECONDS="0")
    assert second.wait_exit(10) == 1
    assert time.monotonic() - started < 5  # interpreter start-up plus one attempt


def test_second_process_takes_over_when_first_stops(
    database: str, holders: Callable[..., Holder]
) -> None:
    first = holders(database)
    first.wait_for("READY")
    second = holders(database, DEMO_LOCK_WAIT_SECONDS="30")
    time.sleep(1.5)
    assert "READY" not in second.output
    first.process.send_signal(signal.SIGTERM)  # clean stop
    first.wait_exit(5)
    second.wait_for("READY", timeout=5)


def test_released_when_holder_killed(database: str, holders: Callable[..., Holder]) -> None:
    first = holders(database)
    first.wait_for("READY")
    first.process.kill()  # SIGKILL: no cleanup at all
    first.wait_exit(5)
    holders(database, DEMO_LOCK_WAIT_SECONDS="5").wait_for("READY", timeout=10)


def test_stop_signal_during_wait_exits_0_within_a_second(
    database: str, holders: Callable[..., Holder]
) -> None:
    holders(database).wait_for("READY")
    second = holders(database, DEMO_LOCK_WAIT_SECONDS="60")
    time.sleep(2)  # interpreter started and waiting for the lock
    signalled = time.monotonic()
    second.process.send_signal(signal.SIGTERM)
    assert second.wait_exit(5) == 0
    assert time.monotonic() - signalled < 1.5
    assert "Running migrations" not in second.output


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("password", "wrongpw-7Q9", "password authentication failed"),
        ("database", "no_such_db_for_lock", "does not exist"),
    ],
    ids=["wrong-password", "missing-database"],
)
def test_permanent_connection_error_refused_at_once(
    database: str, holders: Callable[..., Holder], field: str, value: str, expected: str
) -> None:
    base = make_url(database)
    changed = base.set(password=value) if field == "password" else base.set(database=value)
    url = changed.render_as_string(hide_password=False)
    started = time.monotonic()
    holder = holders(url, DEMO_LOCK_WAIT_SECONDS="60")
    assert holder.wait_exit(10) == 1
    assert time.monotonic() - started < 5  # no retry within the 60 s wait
    assert "Demo mode could not connect to the database: " in holder.output
    assert expected in holder.output
    assert "wrongpw-7Q9" not in holder.output


# ---------------------------------------------------------------------------
# RF-6: the lock connection's session
# ---------------------------------------------------------------------------


def test_keepalive_applied_to_lock_session(database: str) -> None:
    lock = DemoLock.acquire(_config(keepalive=30), database, stop=threading.Event())
    assert lock is not None
    try:
        with lock._conn.cursor() as cursor:  # the lock's own session
            values = []
            for name in ("tcp_keepalives_idle", "tcp_keepalives_interval", "tcp_keepalives_count"):
                cursor.execute(f"SHOW {name}")
                values.append(int(cursor.fetchone()[0]))
        assert tuple(values) == keepalive_settings(30)
    finally:
        lock.close()


# ---------------------------------------------------------------------------
# RF-7, RF-7a: heartbeat, watchdog, hard exit, lock status
# ---------------------------------------------------------------------------


def test_holder_exits_1_when_lock_connection_terminated_and_restart_retakes(
    database: str, holders: Callable[..., Holder]
) -> None:
    first = holders(database)
    first.wait_for("READY")
    terminated = time.monotonic()
    _terminate(database, _lock_backend_pid(database))
    assert first.wait_exit(10) == 1
    assert time.monotonic() - terminated < 2 + 1 + 2 + 0.5  # interval + timeout + 2 s
    assert "Demo lock lost (" in first.output
    holders(database).wait_for("READY")  # the restarted process takes the lock again


def test_watchdog_ends_a_hanging_heartbeat(database: str, holders: Callable[..., Holder]) -> None:
    holder = holders(database, HOLDER_HANG_HEARTBEAT="1")
    ready = holder.wait_for("READY")
    assert holder.wait_exit(10) == 1
    assert time.monotonic() - ready < 2 + 1 + 2 + 0.5
    assert "heartbeat timed out after 1 s" in holder.output


def test_is_held_reflects_the_database(database: str) -> None:
    lock = DemoLock.acquire(_config(), database, stop=threading.Event())
    assert lock is not None
    try:
        assert lock.is_held() is True
        _terminate(database, _lock_backend_pid(database))
        time.sleep(0.2)
        assert lock.is_held() is False
    finally:
        lock.close()


# ---------------------------------------------------------------------------
# RF-1, RF-4 step 6: migrations
# ---------------------------------------------------------------------------


def test_stop_during_migrations_exits_0_and_ends_the_child(
    database: str, holders: Callable[..., Holder]
) -> None:
    holder = holders(database, HOLDER_MIGRATION="slow")
    holder.wait_for("MIGRATION_CHILD")
    child_pid = int(next(line for line in holder.lines if "MIGRATION_CHILD" in line).split()[-1])
    signalled = time.monotonic()
    holder.process.send_signal(signal.SIGTERM)
    assert holder.wait_exit(5) == 0
    assert time.monotonic() - signalled < 1.5
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_logging_stays_info_after_real_migrations(
    database: str, holders: Callable[..., Holder]
) -> None:
    holder = holders(database, HOLDER_MIGRATION="real")
    holder.wait_for("READY", timeout=60)
    output = holder.output
    assert output.index("Running migrations") < output.index("stand-in server started")
    assert "[INFO] tests.lock_holder: stand-in server started with workers=1" in output
