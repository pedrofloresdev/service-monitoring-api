"""Test entry point: the real start command with a stand-in server (spec 003d plan D10).

Run as `python -m tests.postgres.lock_holder` with the demo-mode environment. It runs
`app.serve.main` unchanged (settings, WEB_CONCURRENCY, lock, heartbeat, migrations, signals,
exit paths) but replaces the server, so no service is seeded or checked and nothing touches
the network. Test-only switches:

- HOLDER_MIGRATION: `real` (Alembic, the default), `noop`, or `slow` (prints its pid, sleeps).
- HOLDER_HANG_HEARTBEAT=1: the heartbeat query becomes `pg_sleep(30)`, to exercise the watchdog.
"""

import logging
import os
import sys
import threading
import time
from functools import partial
from typing import Any

from app import serve
from app.services import demo_lock

logger = logging.getLogger("tests.lock_holder")

SLOW_MIGRATION = [
    sys.executable,
    "-c",
    "import os, time; print(f'MIGRATION_CHILD {os.getpid()}', flush=True); time.sleep(30)",
]
NOOP_MIGRATION = [sys.executable, "-c", "pass"]


def stand_in_server(app: str, **options: Any) -> None:
    logger.info("stand-in server started with workers=%s", options.get("workers"))
    print("READY", flush=True)
    while True:  # SIGTERM now has its default action and ends the process
        time.sleep(0.1)


def main() -> int:
    mode = os.environ.get("HOLDER_MIGRATION", "real")
    migrate: serve.Migrate = serve.run_migrations
    if mode == "slow":
        migrate = partial(serve.run_migrations, command=SLOW_MIGRATION)
    elif mode == "noop":
        migrate = partial(serve.run_migrations, command=NOOP_MIGRATION)
    if os.environ.get("HOLDER_HANG_HEARTBEAT") == "1":
        demo_lock.HELD_SQL = "SELECT pg_sleep(30)"
    assert threading.current_thread() is threading.main_thread()
    return serve.main(migrate=migrate, run_server=stand_in_server)


if __name__ == "__main__":
    sys.exit(main())
