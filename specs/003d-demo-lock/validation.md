# Validation - 003d Demo mode: project start command and single-process lock

**Verdict (2026-11-25): SPEC FULFILLED.** CI: PR #9 run 45721830959 on `55b406e`, all 5 checks pass (lint, format, typecheck, test, migrations).
- `/sdd-validate` (reviewer on Sonnet, 2026-11-25): every RF and RNF is covered by passing tests. The reviewer re-ran all the checks and the real `python -m app.serve` against PostgreSQL. It found no spec violation and no doc mismatch.
- Five non-blocking findings. Finding 1 is fixed in the commit after `9aed7f7`: the "lock lost" callbacks now run in parallel within the 1 s budget, so a slow "stop demo jobs" can't keep the migration-child kill from running (RF-7). New test: `test_a_hanging_callback_does_not_stop_a_later_one`. Findings 2 to 5 are accepted:
  - a stop signal in the tiny window just before the server installs its handlers is dropped, and Docker's grace period ends the process;
  - in demo mode an invalid `PORT` is reported before the demo settings;
  - a connect attempt abandoned at its timeout may briefly leak a connection that holds no lock;
  - the automated suite uses a stand-in server, and the real server with the marker was checked by hand.
- The reviewer also noted that after `docker stop`, the server step may exit 143 (uvicorn re-raises the signal), which the spec allows. As PID 1 the container exits 0. Local evidence was collected on 2026-11-25 on WSL2 with Python 3.11, Docker, and PostgreSQL 15.19. `/sdd-validate` re-runs everything rather than trusting this file.

Branch: `feature/003d-demo-lock`.

Commits:
- T1 `a24eb67`
- T2 `4cd941a`
- T3 `d40c033`
- T4 `4bbfc99`
- T5 `0e67b51`
- T6 `a3048d2`
- T7 `97a0b81`
- T8 `e06c207`
- T9 `5a09ced`
- T10 (this commit), which also moves the `PORT` check after `WEB_CONCURRENCY` in demo mode (RF-3 "first")

## Completion criteria

| Criterion | Result | Evidence |
|---|---|---|
| The container starts through the start command as its main process, with demo mode on and off. With demo mode off, migrations and the server behave as before | PASS | `tests/test_container.py` checks two things: the exec-form `CMD ["python", "-m", "app.serve"]` and `--start-period=180s`. Compose, demo mode off, on a throwaway `check_off` database: `/proc/1/cmdline` = `python -m app.serve`; log shows `Running migrations`, the five Alembic upgrades, then `Starting server (demo mode off)` and `Started server process [1]`; `GET /health` → `{"status":"ok"}`, `GET /api/v1/status` → 200; `docker stop` took 0.77 s with exit 0. Compose, demo mode on (`check_on`): `Demo lock acquired`, `Running migrations`, `Starting server (demo mode on)`, then seeding and the seed checked UP; healthcheck reached `healthy` |
| A second process with demo mode on against the same database waits, then refuses. It starts once the first stops. A killed first process doesn't block it, and a stop signal during the wait ends it within a second | PASS | PostgreSQL (`tests/postgres/test_demo_lock.py`): `test_second_process_waits_then_refuses` (2 s wait), `test_wait_zero_is_one_attempt`, `test_second_process_takes_over_when_first_stops`, `test_released_when_holder_killed` (SIGKILL), `test_stop_signal_during_wait_exits_0_within_a_second` (< 1.5 s, exit 0). Compose: a second container with `DEMO_LOCK_WAIT_SECONDS=5` logged the "another process holds the demo lock" error and exited 1 after 6 s; `docker stop` of a third container during its 60 s wait took 0.36 s with exit 0 (`Stop signal received; exiting`); after `docker stop` of the holder (0.77 s, exit 0) a waiting container logged `Demo lock acquired`, then `Demo seeding: 0 created, 1 adopted`, and started |
| Migrations run only after the lock is taken | PASS | `tests/test_serve.py::test_steps_in_order_with_demo_options` checks the step order: settings → lock → heartbeat → migrations → server. `test_second_process_waits_then_refuses` asserts the refused process never logs `Running migrations`. Compose logs show `Demo lock acquired` before `Running migrations` |
| Ending the holder's lock connection makes it exit within the heartbeat bound, and a restart retakes the lock | PASS | `test_holder_exits_1_when_lock_connection_terminated_and_restart_retakes`: `pg_terminate_backend` on the lock connection → exit 1 within interval 2 s + timeout 1 s + 2 s, then a new process gets `READY`. `test_watchdog_ends_a_hanging_heartbeat`: a `pg_sleep(30)` heartbeat → exit 1 within the bound, logging `heartbeat timed out after 1 s` |
| `WEB_CONCURRENCY` above 1, a non-PostgreSQL database and a start through another command each refuse with the documented message | PASS | `tests/test_serve.py` checks each refusal with its exact text: `Demo mode requires a single process (WEB_CONCURRENCY=2)`, invalid values, and `Demo mode requires PostgreSQL`. `tests/test_demo_startup.py::test_demo_mode_without_the_marker_refuses_before_any_demo_step` covers a start without the marker. Compose: `WEB_CONCURRENCY=2` → the exact error; `uvicorn app.main:app` in the image → `RuntimeError: Demo mode must be started with the project start command (python -m app.serve)`, then `Application startup failed` |
| Docs updated per RF-10; quality checks green in CI | PASS | Docs: `docs/architecture.md` §8 covers the start command, order, marker, messages and exit statuses, the lock, key and name, connection options, keepalive, heartbeat and watchdog, hard exit, lock status, the known overlap limit, settings, PostgreSQL directly, `WEB_CONCURRENCY`, stop-first deploys, and the 157 s worst case with the 180 s start period. Also updated: architecture §2 and §7, `README.md` (run commands, the "One process only" section, `PORT` and `WEB_CONCURRENCY` rows), `AGENTS.md` Run line, `docs/SPEC.md` scope line, `.env.example`. Local: `ruff check .` clean, `ruff format --check .` 103 files, `mypy` reports no issues in 63 files, `pytest` 678 passed with coverage 97.23% (floor 88), `python -m tools.schema_check` reports no differences, `pytest -m postgres --no-cov` 33 passed |

## Requirements

| Req | Result | Evidence |
|---|---|---|
| RF-1 | PASS | Tests: the demo-off path, `PORT`, the `.env` file, settings errors without values, and migrations through the child process (command, resolved `DATABASE_URL`, failure status, stop). `test_logging_stays_info_after_real_migrations` (PostgreSQL). Compose |
| RF-2 | PASS | The marker check runs before `load_demo_config`. `serve` sets the marker before the server. Compose run with `uvicorn` |
| RF-3 | PASS | `test_demo_lock_rules.py::TestWebConcurrency`. `test_web_concurrency_is_checked_first` (before settings, with a bad 003a setting present). `test_web_concurrency_comes_before_port` |
| RF-4 | PASS | Order and each failure stopping the later steps (`test_serve.py`). Stop during migrations, with the fake (exit 0, lock released) and on PostgreSQL with a real child process (`test_stop_during_migrations_exits_0_and_ends_the_child`). The server gets the default signal handlers |
| RF-5 | PASS | `test_demo_lock.py::TestAcquire` (1 s cadence on an injected clock, wait 0, unreachable vs held texts, permanent errors without the password, stop during a hanging connect < 0.3 s). PostgreSQL tests for each process-level case, including wrong password and missing database refused in < 5 s with a 60 s wait |
| RF-6 | PASS | `keepalive_settings` detection is below K for every K in 10–600. `test_keepalive_applied_to_lock_session` (`SHOW` on the lock session gives 10, 3, 3 for K=30). Release on clean stop and on SIGKILL |
| RF-7 | PASS | `TestHeartbeat` and `TestLost`: exit 1 within timeout + 2 s for a failed, not-held or hung heartbeat; callbacks first, then a log flush, then the exit; a hanging callback is bounded. The migration child is killed on loss. PostgreSQL termination and watchdog tests |
| RF-7a | PASS | `TestIsHeld` (fresh check, never exits). `runtime.demo_lock_is_held`. `test_is_held_reflects_the_database` |
| RF-8 | PASS | Demo off: no lock, no `WEB_CONCURRENCY` rule, SQLite accepted, invalid lock settings ignored, marker ignored |
| RF-9 | PASS | `tests/test_container.py`. Compose (PID 1, `docker stop` timings, healthy) |
| RF-10 | PASS | Docs listed above |
| RNF-1 | PASS | The existing suite is unchanged. The OpenAPI snapshot test passes. Compose with demo mode off |
| RNF-2 | PASS | No migration; `schema_check` reports no differences |
| RNF-3 | PASS | The lock uses `psycopg2.connect` with `connect_kwargs`, never the engine (`test_connector_gets_the_lock_connection_options_not_the_engine`) |
| RNF-4 | PASS | 13 PostgreSQL tests in `tests/postgres/test_demo_lock.py` |
| RNF-5 | PASS | Plan §12 approved on 2026-11-25 |
| RNF-6 | PASS | Injected clock in `TestAcquire`; 1 s timeouts and 2 s heartbeats elsewhere; defaults never used for timing |
| RNF-7 | PASS | Checks above; CI green on PR #9 |

## Notes

- **T1:** before the lock code was written, it verified the PostgreSQL facts the plan had taken from the documentation (plan §1): `pg_locks` columns, keepalive through `options`, `options` precedence, and the libpq messages. No design change was needed.
- **psycopg2 typing:** psycopg2 has no type stubs. Like APScheduler, it is listed under `ignore_missing_imports` in `pyproject.toml`, rather than adding `types-psycopg2` as a new dependency.
- **Manual runs:** they used throwaway databases (`check_off`, `check_on`) through scratch compose overrides that were not committed. The containers and databases were removed afterwards.

## Pending

None. Merge PR #9 (owner).
