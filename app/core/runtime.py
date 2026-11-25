"""Process-wide state set by the project start command (spec 003d RF-2, RF-7a).

This lives in an application module, not in `app/serve.py`: `python -m app.serve` runs that
file as `__main__`, a separate module object, so a flag there would never be seen by the app.
Processes spawned by auto-reload or extra workers start a fresh interpreter, so they don't
have the flag.
"""

from __future__ import annotations

from typing import Protocol

START_COMMAND_MESSAGE = (
    "Demo mode must be started with the project start command (python -m app.serve)"
)


class LockStatus(Protocol):
    def is_held(self) -> bool: ...


_started_by_serve = False
_demo_lock: LockStatus | None = None


def mark_started_by_serve() -> None:
    """Set by the start command in this process, just before the server starts."""
    global _started_by_serve
    _started_by_serve = True


def started_by_serve() -> bool:
    return _started_by_serve


def set_demo_lock(lock: LockStatus) -> None:
    global _demo_lock
    _demo_lock = lock


def demo_lock_is_held() -> bool:
    """A fresh check that this process holds the demo lock (RF-7a); False without a lock."""
    return _demo_lock is not None and _demo_lock.is_held()
