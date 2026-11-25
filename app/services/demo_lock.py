"""The demo lock: one process per database while demo mode is on (spec 003d RF-5 to RF-7a).

The lock is a session-level PostgreSQL advisory lock held on a dedicated autocommit
psycopg2 connection, outside the pool used by requests and checks (RNF-3). PostgreSQL
releases it when that connection ends (RF-6).

Every connect attempt and query runs in a short-lived daemon thread that the caller waits
for with a deadline: a blocking call on a dead network ignores statement timeouts, but an
abandoned daemon thread never keeps the process alive (plan D3).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any, Literal

import psycopg2

from app.core.demo_settings import LockConfig
from app.services.demo_lock_rules import (
    CONNECT_TIMEOUT_SECONDS,
    classify_connect_error,
    connect_kwargs,
    lock_database,
    lock_key,
    public_reason,
    split_key,
)

logger = logging.getLogger(__name__)

RETRY_SECONDS = 1.0  # lock retry cadence (RF-5)
STOP_POLL_SECONDS = 0.1  # how often waits look at the stop event
CALLBACK_BUDGET_SECONDS = 1.0  # time allowed for "lock lost" callbacks before the exit (RF-7)

HELD_BY_OTHER_MESSAGE = "Demo mode requires a single process: another process holds the demo lock"
UNREACHABLE_MESSAGE = "Demo mode could not reach the database to take the demo lock"
PERMANENT_PREFIX = "Demo mode could not connect to the database: "

TRY_LOCK_SQL = "SELECT pg_try_advisory_lock(%(key)s)"
HELD_SQL = """
SELECT EXISTS (
    SELECT 1 FROM pg_locks
    WHERE locktype = 'advisory' AND granted AND pid = pg_backend_pid()
      AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND classid = %(classid)s AND objid = %(objid)s AND objsubid = 1
)
"""

Connector = Callable[..., Any]
Clock = Callable[[], float]
Sleep = Callable[[float, threading.Event], bool]  # returns True when stopped
ExitFunction = Callable[[int], object]
Outcome = tuple[Literal["ok", "error", "timeout", "stopped"], Any]


class LockRefused(Exception):
    """The demo lock can't be taken; the start command refuses with this message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _wait(seconds: float, stop: threading.Event) -> bool:
    return stop.wait(seconds)


def _bounded(
    work: Callable[[], Any], timeout: float, stop: threading.Event | None = None
) -> Outcome:
    """Run `work` in a daemon thread; give up at the timeout or when `stop` is set."""
    box: dict[str, Any] = {}
    done = threading.Event()

    def target() -> None:
        try:
            box["value"] = work()
        except BaseException as exc:  # reported to the caller, never lost
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=target, daemon=True, name="demo-lock-call").start()
    deadline = time.monotonic() + timeout
    while not done.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout", None
        if stop is not None and stop.is_set():
            return "stopped", None
        done.wait(min(remaining, STOP_POLL_SECONDS))
    if "error" in box:
        return "error", box["error"]
    return "ok", box.get("value")


def _query_one(conn: Any, sql: str, params: dict[str, int]) -> Any:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def _close(conn: Any) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # closing a broken connection may fail; it is gone either way
        logger.debug("Closing the lock connection failed", exc_info=True)


class DemoLock:
    def __init__(
        self,
        conn: Any,
        key: int,
        config: LockConfig,
        *,
        exit_fn: ExitFunction = os._exit,
    ) -> None:
        self._conn = conn
        self._key = key
        self._classid, self._objid = split_key(key)
        self._config = config
        self._exit = exit_fn
        self._conn_lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self._heartbeat_stop = threading.Event()

    # -- taking the lock (RF-5) -----------------------------------------------------------

    @classmethod
    def acquire(
        cls,
        config: LockConfig,
        database_url: str,
        *,
        stop: threading.Event,
        connect: Connector = psycopg2.connect,
        clock: Clock = time.monotonic,
        sleep: Sleep = _wait,
        exit_fn: ExitFunction = os._exit,
    ) -> DemoLock | None:
        """Take the lock, retrying every second until the wait ends; None when stopped."""
        database = lock_database(database_url)
        key = lock_key(config.name, database)
        kwargs = connect_kwargs(database_url, config.keepalive)
        password = kwargs.get("password")
        deadline = clock() + config.wait
        reached = False
        conn: Any = None
        while True:
            attempt_started = clock()
            if conn is None:
                status, value = _bounded(
                    lambda: connect(**kwargs), CONNECT_TIMEOUT_SECONDS + 1, stop
                )
                if status == "stopped":
                    return None
                if status == "ok":
                    conn = value
                    conn.autocommit = True
                    reached = True
                elif status == "error" and classify_connect_error(str(value)) == "permanent":
                    raise LockRefused(PERMANENT_PREFIX + public_reason(str(value), password))
            if conn is not None:
                try_lock = partial(_query_one, conn, TRY_LOCK_SQL, {"key": key})
                status, value = _bounded(try_lock, config.timeout, stop)
                if status == "stopped":
                    _close(conn)
                    return None
                if status == "ok" and value is True:
                    logger.info("Demo lock acquired (name %s, database %s)", config.name, database)
                    return cls(conn, key, config, exit_fn=exit_fn)
                if status != "ok":  # the connection broke: open a new one next time
                    _close(conn)
                    conn = None
            if clock() >= deadline:
                _close(conn)
                raise LockRefused(HELD_BY_OTHER_MESSAGE if reached else UNREACHABLE_MESSAGE)
            pause = min(attempt_started + RETRY_SECONDS, deadline) - clock()
            if (pause > 0 and sleep(pause, stop)) or stop.is_set():
                _close(conn)
                return None

    # -- holding the lock (RF-7, RF-7a) ---------------------------------------------------

    def check(self) -> tuple[bool, str]:
        """Is the key still held by this connection? Bounded by the heartbeat timeout."""
        timeout = self._config.timeout
        started = time.monotonic()
        timed_out = f"heartbeat timed out after {timeout} s"
        if not self._conn_lock.acquire(timeout=timeout):
            return False, timed_out
        params = {"classid": self._classid, "objid": self._objid}

        def query() -> Any:
            try:
                return _query_one(self._conn, HELD_SQL, params)
            finally:
                self._conn_lock.release()  # a hung query keeps it: the connection is unusable

        status, value = _bounded(query, max(0.0, timeout - (time.monotonic() - started)))
        if status == "timeout":
            return False, timed_out
        if status == "error":
            return False, f"heartbeat failed: {type(value).__name__}"
        if value is not True:
            return False, "lock no longer held"
        return True, ""

    def is_held(self) -> bool:
        """A fresh check for other parts of the app (RF-7a); errors count as not held."""
        held, _ = self.check()
        return held

    def add_lost_callback(self, callback: Callable[[], None]) -> None:
        self._callbacks.append(callback)

    def remove_lost_callback(self, callback: Callable[[], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def start_heartbeat(self) -> None:
        threading.Thread(target=self._heartbeat, daemon=True, name="demo-lock-heartbeat").start()

    def _heartbeat(self) -> None:
        while not self._heartbeat_stop.wait(self._config.heartbeat):
            held, reason = self.check()
            if not held and not self._heartbeat_stop.is_set():
                self.lost(reason)
                return

    def lost(self, reason: str) -> None:
        """Stop the demo jobs and end the process at once with status 1 (RF-7, R1)."""
        logger.error("Demo lock lost (%s); stopping demo jobs and exiting", reason)

        def run(callback: Callable[[], None]) -> None:
            try:
                callback()
            except Exception:
                logger.exception("A demo-lock-lost callback failed")

        # Each callback in its own thread, all within one budget, so a slow one (for example
        # stopping the scheduler) never keeps a later one (killing the migration child) from
        # running.
        workers = [
            threading.Thread(target=run, args=(callback,), daemon=True, name="demo-lock-lost")
            for callback in self._callbacks
        ]
        for worker in workers:
            worker.start()
        deadline = time.monotonic() + CALLBACK_BUDGET_SECONDS
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        for handler in logging.getLogger().handlers:
            handler.flush()
        self._exit(1)

    def close(self) -> None:
        """Stop the heartbeat and close the lock connection, which releases the lock."""
        self._heartbeat_stop.set()
        _close(self._conn)
