"""The project start command: `python -m app.serve` (spec 003d RF-1 to RF-5, RF-8).

In every mode it loads the settings, runs the migrations in a child process and then runs
the server in this process. While demo mode is on it first checks `WEB_CONCURRENCY`,
validates every demo setting, requires PostgreSQL, takes the demo lock and starts its
heartbeat, so migrations run only once the lock is held.

The settings module is imported inside `main`, because importing it builds the settings
and an invalid environment must give a clean error, not a traceback.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core import runtime
from app.core.demo_settings import DemoConfigError, LockConfig, validate_demo_settings
from app.services.demo_lock import STOP_POLL_SECONDS, DemoLock, LockRefused
from app.services.demo_lock_rules import parse_port, parse_web_concurrency

logger = logging.getLogger("app.serve")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_COMMAND = [sys.executable, "-m", "alembic", "upgrade", "head"]
APP = "app.main:app"
HOST = "0.0.0.0"
STOPPED = -1  # run_migrations: ended by a stop signal
TERMINATE_GRACE_SECONDS = 0.5


class LostCallbacks(Protocol):
    def add_lost_callback(self, callback: Callable[[], None]) -> None: ...

    def remove_lost_callback(self, callback: Callable[[], None]) -> None: ...


class HeldLock(LostCallbacks, Protocol):
    def start_heartbeat(self) -> None: ...

    def is_held(self) -> bool: ...

    def close(self) -> None: ...


Acquire = Callable[..., HeldLock | None]
Migrate = Callable[[str, threading.Event, HeldLock | None], int]
RunServer = Callable[..., Any]
Handlers = tuple[Any, Any]


# -- server options (RF-1; spec 003b adds its demo options in demo_server_options) --------


def server_options(demo: bool, port: int) -> dict[str, Any]:
    if demo:
        return demo_server_options(port)
    # workers=None lets uvicorn read WEB_CONCURRENCY itself, exactly as before (RNF-1).
    return {"host": HOST, "port": port, "workers": None, "reload": False}


def demo_server_options(port: int) -> dict[str, Any]:
    """The single place for demo-mode server options (RF-1)."""
    return {"host": HOST, "port": port, "workers": 1, "reload": False}


# -- signals ------------------------------------------------------------------------------


def install_stop_handlers(stop: threading.Event) -> Handlers | None:
    """Until the server starts, SIGTERM and SIGINT only set `stop` (RF-4, RF-5, plan D9)."""
    if threading.current_thread() is not threading.main_thread():
        return None

    def handler(signum: int, frame: FrameType | None) -> None:
        stop.set()

    return signal.signal(signal.SIGTERM, handler), signal.signal(signal.SIGINT, handler)


def restore_handlers(previous: Handlers | None) -> None:
    if previous is not None:
        signal.signal(signal.SIGTERM, previous[0])
        signal.signal(signal.SIGINT, previous[1])


# -- migrations (RF-1, RF-4 step 6) -------------------------------------------------------


def _end(child: subprocess.Popen[bytes]) -> None:
    child.terminate()
    try:
        child.wait(TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def run_migrations(
    database_url: str,
    stop: threading.Event,
    lock: LostCallbacks | None,
    *,
    command: list[str] | None = None,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    """Run Alembic in a child process, so its logging setup never reaches the server (R11).

    Returns the child's exit status, or STOPPED when a stop signal ended it. A lost lock
    kills the child (RF-7).
    """
    child = popen(
        command or MIGRATION_COMMAND,
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
    )

    def kill() -> None:
        child.kill()

    if lock is not None:
        lock.add_lost_callback(kill)
    try:
        while True:
            status = child.poll()
            if status is not None:
                return status
            if stop.wait(STOP_POLL_SECONDS):
                _end(child)
                return STOPPED
    finally:
        if lock is not None:
            lock.remove_lost_callback(kill)


# -- demo jobs ----------------------------------------------------------------------------


def stop_demo_jobs() -> None:
    """Called when the lock is lost (RF-7). Spec 003c registers its jobs too."""
    from app.workers import scheduler

    scheduler.stop_scheduler()


# -- the command --------------------------------------------------------------------------


def _settings_errors(exc: ValidationError) -> str:
    lines = [f"  - {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return "Invalid settings:\n" + "\n".join(lines)


def _serve(options: dict[str, Any], lock: HeldLock | None, run_server: RunServer) -> int:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)  # the server owns stop signals from now on
    signal.signal(signal.SIGINT, signal.default_int_handler)
    runtime.mark_started_by_serve()  # same process, just before the server (RF-2)
    logger.info("Starting server (demo mode %s)", "on" if lock is not None else "off")
    try:
        run_server(APP, **options)
    finally:
        if lock is not None:
            lock.close()
    return 0


def _migrate_then_serve(
    database_url: str,
    stop: threading.Event,
    lock: HeldLock | None,
    options: dict[str, Any],
    migrate: Migrate,
    run_server: RunServer,
) -> int:
    logger.info("Running migrations")
    status = migrate(database_url, stop, lock)
    if stop.is_set():
        logger.info("Stop signal received; exiting")
        if lock is not None:
            lock.close()
        return 0
    if status != 0:
        logger.error("Migrations failed (exit status %d)", status)
        if lock is not None:
            lock.close()
        return 1
    return _serve(options, lock, run_server)


def _demo(
    database_url: str,
    stop: threading.Event,
    acquire: Acquire,
    migrate: Migrate,
    run_server: RunServer,
) -> int:
    workers = parse_web_concurrency(os.environ.get("WEB_CONCURRENCY"))  # RF-3, first
    if workers > 1:
        logger.error("Demo mode requires a single process (WEB_CONCURRENCY=%d)", workers)
        return 1
    port = parse_port(os.environ.get("PORT"))  # after WEB_CONCURRENCY, which comes first
    try:
        _, lock_config = validate_demo_settings()  # every error at once
    except DemoConfigError as exc:
        logger.error("%s", exc)
        return 1
    if make_url(database_url).get_backend_name() != "postgresql":
        logger.error("Demo mode requires PostgreSQL")
        return 1
    try:
        lock = acquire(lock_config, database_url, stop=stop)
    except LockRefused as exc:
        logger.error("%s", exc.message)
        return 1
    if lock is None:
        logger.info("Stop signal received; exiting")
        return 0
    runtime.set_demo_lock(lock)
    lock.add_lost_callback(stop_demo_jobs)
    lock.start_heartbeat()
    return _migrate_then_serve(
        database_url, stop, lock, demo_server_options(port), migrate, run_server
    )


def _acquire_lock(
    config: LockConfig, database_url: str, *, stop: threading.Event
) -> DemoLock | None:
    return DemoLock.acquire(config, database_url, stop=stop)


def _run_server(app: str, **options: Any) -> None:
    import uvicorn

    uvicorn.run(app, **options)


def main(
    *,
    acquire: Acquire = _acquire_lock,
    migrate: Migrate = run_migrations,
    run_server: RunServer = _run_server,
) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    stop = threading.Event()
    previous = install_stop_handlers(stop)
    try:
        try:
            from app.core import config

            settings = config.Settings()
        except ValidationError as exc:
            logger.error("%s", _settings_errors(exc))
            return 1
        try:
            if not settings.demo_mode:  # RF-8: no lock, no WEB_CONCURRENCY rule, any database
                port = parse_port(os.environ.get("PORT"))
                return _migrate_then_serve(
                    settings.DATABASE_URL,
                    stop,
                    None,
                    server_options(False, port),
                    migrate,
                    run_server,
                )
            return _demo(settings.DATABASE_URL, stop, acquire, migrate, run_server)
        except ValueError as exc:  # PORT and WEB_CONCURRENCY carry their own message
            logger.error("%s", exc)
            return 1
    finally:
        restore_handlers(previous)


if __name__ == "__main__":
    sys.exit(main())
