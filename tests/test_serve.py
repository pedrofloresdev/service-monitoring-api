"""The project start command with stand-ins for the lock, migrations and server
(spec 003d RF-1 to RF-4, RF-8). Real processes and PostgreSQL: tests/postgres/test_demo_lock.py.
"""

import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app import serve
from app.core import demo_settings, runtime
from app.core.demo_settings import LockConfig
from app.services.demo_lock import LockRefused

PG_URL = "postgresql://demo:pw@db:5432/app"
SEED = '[{"name": "Example", "url": "https://example.com/"}]'
ENV_VARS = (
    "DATABASE_URL",
    "DEMO_MODE",
    "DEMO_SEED_SERVICES",
    "DEMO_GLOBAL_VISITOR_CAP",
    "DEMO_LOCK_WAIT_SECONDS",
    "DEMO_LOCK_NAME",
    "WEB_CONCURRENCY",
    "PORT",
)


class FakeLock:
    def __init__(self, steps: list[str]) -> None:
        self.steps = steps
        self.callbacks: list[Any] = []
        self.closed = False

    def add_lost_callback(self, callback: Any) -> None:
        self.callbacks.append(callback)

    def remove_lost_callback(self, callback: Any) -> None:
        self.callbacks.remove(callback)

    def start_heartbeat(self) -> None:
        self.steps.append("heartbeat")

    def is_held(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


class Harness:
    """Records every step the start command takes."""

    def __init__(self) -> None:
        self.steps: list[str] = []
        self.lock = FakeLock(self.steps)
        self.lock_result: Any = self.lock
        self.migration_status = 0
        self.migration_urls: list[str] = []
        self.server_calls: list[dict[str, Any]] = []
        self.server_state: dict[str, Any] = {}
        self.acquired_with: list[tuple[LockConfig, str]] = []
        self.stop_during_migrations = False

    def acquire(self, config: LockConfig, url: str, *, stop: threading.Event) -> Any:
        self.steps.append("lock")
        self.acquired_with.append((config, url))
        if isinstance(self.lock_result, Exception):
            raise self.lock_result
        return self.lock_result

    def migrate(self, url: str, stop: threading.Event, lock: Any) -> int:
        self.steps.append("migrations")
        self.migration_urls.append(url)
        if self.stop_during_migrations:
            stop.set()
            return -1
        return self.migration_status

    def run_server(self, app: str, **options: Any) -> None:
        self.steps.append("server")
        self.server_calls.append({"app": app, **options})
        self.server_state = {
            "sigterm": signal.getsignal(signal.SIGTERM),
            "sigint": signal.getsignal(signal.SIGINT),
            "marker": runtime.started_by_serve(),
        }

    def main(self) -> int:
        return serve.main(acquire=self.acquire, migrate=self.migrate, run_server=self.run_server)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Harness:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env unless a test writes one
    monkeypatch.setattr(runtime, "_started_by_serve", False)
    monkeypatch.setattr(runtime, "_demo_lock", None)
    h = Harness()
    real_validate = demo_settings.validate_demo_settings

    def validate() -> Any:
        h.steps.append("settings")
        return real_validate()

    monkeypatch.setattr(serve, "validate_demo_settings", validate)
    return h


def _demo_env(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_SEED_SERVICES", SEED)
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]


# ---------------------------------------------------------------------------
# RF-1, RF-8: demo mode off
# ---------------------------------------------------------------------------


class TestDemoModeOff:
    def test_migrations_then_server_with_todays_options(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", PG_URL)
        assert harness.main() == 0
        assert harness.steps == ["migrations", "server"]
        assert harness.migration_urls == [PG_URL]
        assert harness.server_calls == [
            {
                "app": "app.main:app",
                "host": "0.0.0.0",
                "port": 8000,
                "workers": None,
                "reload": False,
            }
        ]

    def test_no_lock_no_single_process_rule_no_postgresql_requirement(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite:///./local.db")
        monkeypatch.setenv("WEB_CONCURRENCY", "3")  # left to uvicorn, as today
        monkeypatch.setenv("DEMO_LOCK_NAME", "not a valid name!")  # never read
        assert harness.main() == 0
        assert "lock" not in harness.steps and "settings" not in harness.steps
        assert harness.server_calls[0]["workers"] is None

    def test_port(self, harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", PG_URL)
        monkeypatch.setenv("PORT", "9000")
        assert harness.main() == 0
        assert harness.server_calls[0]["port"] == 9000

    def test_invalid_port_refuses(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", PG_URL)
        monkeypatch.setenv("PORT", "80.0")
        assert harness.main() == 1
        assert _errors(caplog) == ["PORT: must be a whole number from 1 to 65535"]
        assert harness.steps == []

    def test_settings_come_from_the_env_file(self, harness: Harness, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("DATABASE_URL=postgresql://from-file:pw@db/app\n")
        assert harness.main() == 0
        assert harness.migration_urls == ["postgresql://from-file:pw@db/app"]

    def test_invalid_settings_refuse_without_echoing_values(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", PG_URL)
        monkeypatch.setenv("DEMO_MODE", "secret-maybe")
        assert harness.main() == 1
        [message] = _errors(caplog)
        assert message.startswith("Invalid settings:") and "DEMO_MODE" in message
        assert "secret-maybe" not in message
        assert harness.steps == []

    def test_missing_database_url_refuses(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        assert harness.main() == 1
        assert "DATABASE_URL" in _errors(caplog)[0]

    def test_failed_migrations_refuse(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", PG_URL)
        harness.migration_status = 2
        assert harness.main() == 1
        assert _errors(caplog) == ["Migrations failed (exit status 2)"]
        assert "server" not in harness.steps

    def test_stop_during_migrations_exits_0(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", PG_URL)
        harness.stop_during_migrations = True
        assert harness.main() == 0
        assert "server" not in harness.steps


# ---------------------------------------------------------------------------
# RF-2 to RF-4: demo mode on
# ---------------------------------------------------------------------------


class TestDemoModeOn:
    def test_steps_in_order_with_demo_options(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _demo_env(monkeypatch, PORT="8080")
        assert harness.main() == 0
        assert harness.steps == ["settings", "lock", "heartbeat", "migrations", "server"]
        assert harness.server_calls == [
            {"app": "app.main:app", "host": "0.0.0.0", "port": 8080, "workers": 1, "reload": False}
        ]
        config, url = harness.acquired_with[0]
        assert url == PG_URL and config.name == "service-monitoring-api-demo"
        assert harness.lock.closed  # released once the server returns

    def test_server_gets_default_signal_handlers_and_the_marker(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _demo_env(monkeypatch)
        harness.main()
        assert harness.server_state == {
            "sigterm": signal.SIG_DFL,
            "sigint": signal.default_int_handler,
            "marker": True,
        }

    def test_lock_is_published_and_demo_jobs_stop_on_loss(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _demo_env(monkeypatch)
        harness.main()
        assert runtime._demo_lock is harness.lock
        assert serve.stop_demo_jobs in harness.lock.callbacks

    def test_signal_handlers_are_restored_after_a_refusal(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _demo_env(monkeypatch, WEB_CONCURRENCY="2")
        before = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
        harness.main()
        assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == before

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ("2", "Demo mode requires a single process (WEB_CONCURRENCY=2)"),
            ("0", "WEB_CONCURRENCY: must be a whole number of at least 1"),
            ("two", "WEB_CONCURRENCY: must be a whole number of at least 1"),
        ],
    )
    def test_web_concurrency_is_checked_first(
        self,
        harness: Harness,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        raw: str,
        message: str,
    ) -> None:
        _demo_env(monkeypatch, WEB_CONCURRENCY=raw, DEMO_GLOBAL_VISITOR_CAP="bad")
        assert harness.main() == 1
        assert _errors(caplog) == [message]
        assert harness.steps == []  # before settings validation and any lock attempt

    @pytest.mark.parametrize("raw", ["", "1"])
    def test_web_concurrency_of_one(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        _demo_env(monkeypatch, WEB_CONCURRENCY=raw)
        assert harness.main() == 0

    def test_every_invalid_demo_setting_at_once(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _demo_env(monkeypatch, DEMO_GLOBAL_VISITOR_CAP="0", DEMO_LOCK_WAIT_SECONDS="x")
        assert harness.main() == 1
        [message] = _errors(caplog)
        assert message.startswith("Invalid demo settings:")
        assert "DEMO_GLOBAL_VISITOR_CAP" in message and "DEMO_LOCK_WAIT_SECONDS" in message
        assert harness.steps == ["settings"]

    def test_postgresql_is_required(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _demo_env(monkeypatch, DATABASE_URL="sqlite:///./demo.db")
        assert harness.main() == 1
        assert _errors(caplog) == ["Demo mode requires PostgreSQL"]
        assert harness.steps == ["settings"]

    def test_lock_refused(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _demo_env(monkeypatch)
        harness.lock_result = LockRefused(
            "Demo mode could not reach the database to take the demo lock"
        )
        assert harness.main() == 1
        assert _errors(caplog) == ["Demo mode could not reach the database to take the demo lock"]
        assert harness.steps == ["settings", "lock"]

    def test_stop_during_the_lock_wait_exits_0(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _demo_env(monkeypatch)
        harness.lock_result = None
        assert harness.main() == 0
        assert harness.steps == ["settings", "lock"]

    def test_failed_migrations_refuse_and_release_the_lock(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _demo_env(monkeypatch)
        harness.migration_status = 1
        assert harness.main() == 1
        assert _errors(caplog) == ["Migrations failed (exit status 1)"]
        assert "server" not in harness.steps and harness.lock.closed

    def test_stop_during_migrations_exits_0_and_releases_the_lock(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _demo_env(monkeypatch)
        harness.stop_during_migrations = True
        assert harness.main() == 0
        assert "server" not in harness.steps and harness.lock.closed


def test_stop_demo_jobs_stops_the_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import scheduler

    stopped: list[bool] = []
    monkeypatch.setattr(scheduler, "stop_scheduler", lambda: stopped.append(True))
    serve.stop_demo_jobs()
    assert stopped == [True]


def test_stop_handler_sets_the_event() -> None:
    stop = threading.Event()
    previous = serve.install_stop_handlers(stop)
    try:
        signal.raise_signal(signal.SIGTERM)
        assert stop.is_set()
    finally:
        serve.restore_handlers(previous)


# ---------------------------------------------------------------------------
# RF-1, RF-4 step 6: the migrations child process
# ---------------------------------------------------------------------------


class _Lock:
    def __init__(self) -> None:
        self.callbacks: list[Any] = []

    def add_lost_callback(self, callback: Any) -> None:
        self.callbacks.append(callback)

    def remove_lost_callback(self, callback: Any) -> None:
        self.callbacks.remove(callback)


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class TestRunMigrations:
    def test_default_command_runs_alembic_from_the_project_root(self) -> None:
        assert serve.MIGRATION_COMMAND == [sys.executable, "-m", "alembic", "upgrade", "head"]
        assert (serve.PROJECT_ROOT / "alembic.ini").is_file()

    def test_child_gets_the_resolved_database_url(self) -> None:
        code = (
            "import os, sys; sys.exit(0 if os.environ['DATABASE_URL'] == 'postgresql://x/y' else 5)"
        )
        status = serve.run_migrations(
            "postgresql://x/y", threading.Event(), None, command=_python(code)
        )
        assert status == 0

    def test_failure_status_is_returned(self) -> None:
        status = serve.run_migrations(
            "u", threading.Event(), None, command=_python("raise SystemExit(4)")
        )
        assert status == 4

    def test_stop_ends_the_child_within_a_second(self) -> None:
        stop = threading.Event()
        started: list[subprocess.Popen[bytes]] = []

        def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
            child = subprocess.Popen(*args, **kwargs)
            started.append(child)
            return child

        threading.Timer(0.2, stop.set).start()
        t0 = time.monotonic()
        status = serve.run_migrations(
            "u", stop, None, command=_python("import time; time.sleep(30)"), popen=popen
        )
        assert status == serve.STOPPED and time.monotonic() - t0 < 1.5
        assert started[0].poll() is not None

    def test_lost_lock_kills_the_child_and_callback_is_removed(self) -> None:
        lock = _Lock()
        stop = threading.Event()

        def kill_soon() -> None:
            while not lock.callbacks:
                time.sleep(0.01)
            lock.callbacks[0]()

        threading.Thread(target=kill_soon, daemon=True).start()
        status = serve.run_migrations(
            "u",
            stop,
            lock,
            command=_python("import time; time.sleep(30)"),
        )
        assert status != 0 and lock.callbacks == []
