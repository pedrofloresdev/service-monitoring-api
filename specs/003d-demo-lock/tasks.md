# 003d - Demo mode: project start command and single-process lock — tasks

Status: approved (2026-11-25)
Spec: `spec.md` (approved) · Plan: `plan.md` (approved 2026-11-25, including §12 container and start-command changes)

Rules for every task:
- Implement one task, then stop.
- Logic is written test first (red, then green).
- After each task: `ruff check .`, `ruff format --check .`, `mypy`, and `pytest` with coverage ≥ 88.
- When a task touches the lock or the start command, also run `pytest -m postgres --no-cov` once T8 exists (with `docker compose up -d db` and `TEST_POSTGRES_URL`).
- Time-based tests use an injected clock or short configured values, never the defaults (RNF-6).
- Commit each task with `Refs: specs/003d-demo-lock Tn`.

## Tasks

- [x] **T1. Verify the PostgreSQL facts (scratch only).** On PostgreSQL 15 (`docker compose up -d db`), run a scratch script, not committed, that checks the plan §1 facts:
  - how a `bigint` advisory lock appears in `pg_locks` (`classid`, `objid`, `objsubid`);
  - `tcp_keepalives_*` set through `options` and read with `SHOW` over TCP;
  - psycopg2 keyword `options` replacing the URL's `options`;
  - libpq's first-line messages for a wrong password, a missing database, a missing role, a refused port, a blackholed address and an unknown host.

  Plan §1, §6 RF-5, RF-6, RF-7
  - Done when:
    - Plan §1 records the measured results and §6 `classify_connect_error` lists the confirmed messages, adjusted if they differ.
    - The plan change is committed.
    - No project code is changed.

- [x] **T2. Pure lock rules, test first.** Write `tests/test_demo_lock_rules.py` first, then `app/services/demo_lock_rules.py` (plan §6): `parse_web_concurrency`, `parse_port`, `lock_key`, `split_key`, `keepalive_settings`, `merge_options`, `connect_kwargs`, `lock_database`, `classify_connect_error`, `public_reason`. RF-3, RF-5, RF-6
  - Done when:
    - The tests failed first and pass after.
    - `WEB_CONCURRENCY` cases: unset, empty, `1`, `2`, `0`, `+1`, `1.0`, ` 1`, non-ASCII digits. `PORT` cases: unset, `0`, `65536`, `8000`.
    - The lock key is deterministic, signed 64-bit, and differs by name and by database.
    - `split_key` round-trips for negative and positive keys.
    - Keepalive detection is below k for every k in 10–600.
    - `options` precedence, URL query parameters kept, and lock options winning.
    - Every T1 message is classified correctly.
    - The password is removed (raw and percent-encoded).
    - All quality checks are green.

- [x] **T3. Lock settings and combined validation.** Add the five `DEMO_LOCK_*` fields, `LockConfig`, `load_lock_config` and `validate_demo_settings` to `app/core/demo_settings.py` (plan §4.1), and the commented settings to `.env.example`. Tests first in `tests/test_demo_settings.py`. Settings, RF-4 step 2
  - Done when:
    - Tests cover the defaults (60, 10, 5, 30, `service-monitoring-api-demo`), each range, strict integers and the lock-name rule.
    - The timeout-below-interval rule names both variables.
    - One `DemoConfigError` lists every 003a and 003d error together.
    - Malformed lock settings are ignored while demo mode is off.
    - The `.env.example` test passes.
    - All quality checks are green.

- [ ] **T4. Start marker and lock status entry point.** Add `app/core/runtime.py` (plan §3, §4.3). Add the lifespan check that refuses demo mode without the marker, before any 003a step (plan §7.6). Tests first in `tests/test_demo_startup.py`, where the existing demo lifespan tests set the marker through a fixture. RF-2, RF-7a (entry point), RF-8
  - Done when these tests pass:
    - demo mode on without the marker raises the exact RF-2 message, and `load_demo_config` is not called;
    - with the marker it starts;
    - demo mode off ignores the marker;
    - `runtime.demo_lock_is_held()` is false without a lock.

    All quality checks are green.

- [ ] **T5. Taking the lock with fakes.** Write `tests/test_demo_lock.py` first, then `DemoLock.acquire` and `LockRefused` in `app/services/demo_lock.py` (plan §7.2), with an injected connector, clock and sleep. Connect attempts and queries run in daemon threads. RF-5, RF-6 (connection options), RNF-3
  - Done when the tests show:
    - a held lock retried every 1 s until the wait ends;
    - wait 0 is exactly one attempt;
    - unreachable then timeout gives the "could not reach" text;
    - reached then held gives the "another process" text;
    - a permanent error is refused at once with no retry and no password;
    - the stop event during a hanging connect returns `None` within 0.3 s;
    - the connector gets the `connect_kwargs` result, and not the SQLAlchemy engine.

    All quality checks are green.

- [ ] **T6. Heartbeat, watchdog, hard exit and `is_held`.** Add `start_heartbeat`, the bounded query, `lost`, the callbacks, `is_held` and `close` to `DemoLock` (plan §7.3, §7.4), with an injected exit function. Tests first in `tests/test_demo_lock.py`. RF-7, RF-7a
  - Done when the tests show, with a 1 s timeout:
    - a failed, timed-out or not-held heartbeat runs the callbacks and calls exit with 1 within timeout + 2 s;
    - a hanging callback doesn't push the exit past the budget;
    - logs are flushed before the exit;
    - `is_held` is true, false on an error, and false on a hang after the timeout, and it never calls `lost`.

    All quality checks are green.

- [ ] **T7. The project start command with stand-ins.** Write `tests/test_serve.py` first, then `app/serve.py` (plan §7.1, §7.5): settings errors, `PORT`, the demo-off path, the RF-4 sequence, `run_migrations` (child process, stop handling, lost-lock kill), `serve` (handlers reset, marker, `uvicorn.run`, lock closed), and `server_options` / `demo_server_options`. RF-1, RF-3, RF-4, RF-8
  - Done when the tests show:
    - the exact step order with recording fakes;
    - each failure stops the later steps with exit 1 and the exact §5 text;
    - `WEB_CONCURRENCY=2` refuses before settings validation;
    - `sqlite://` refuses with demo mode on, and is accepted with demo mode off with `WEB_CONCURRENCY=3`, with no lock call;
    - the migration child gets `[sys.executable, "-m", "alembic", "upgrade", "head"]` and the resolved `DATABASE_URL`;
    - the default signal handlers are in place when the fake server is called, and the marker is set;
    - the options are `{host 0.0.0.0, port, workers None, reload False}` with demo mode off, and `{..., workers 1, reload False}` with demo mode on;
    - a stop during the lock wait or the migrations returns 0.

    All quality checks are green.

- [ ] **T8. PostgreSQL tests in real processes.** Add `tests/postgres/lock_holder.py`, the stand-in server entry point (plan D10), and `tests/postgres/test_demo_lock.py` (plan §10 PostgreSQL column), with heartbeat 2 s and timeout 1 s. RNF-4, RF-5, RF-6, RF-7, RF-7a, RF-1 (logging)
  - Done when `pytest -m postgres --no-cov` passes, including:
    - wait then refuse, takeover after the first stops, and wait 0;
    - a stop signal during the wait exits 0 within 1.5 s;
    - a wrong password and a missing database are refused at once, with no password in the output;
    - keepalive settings applied to the lock session;
    - release on SIGKILL and on a clean stop;
    - `pg_terminate_backend` causes exit 1 within 5 s, and a restart retakes the lock;
    - the watchdog ends a `pg_sleep` heartbeat;
    - a stop during a slow migration exits 0 with the child gone;
    - INFO logging still works after the real migrations;
    - `is_held` reflects the database.

    The default suite and the quality checks are green.

- [ ] **T9. Container change.** Change the `Dockerfile` to an exec-form `CMD ["python", "-m", "app.serve"]` and `--start-period=180s`, with a comment giving the RF-9 sum. Add `tests/test_container.py`. RF-9, RNF-1, RNF-5
  - Done when the static test passes and a manual `docker compose up --build` shows:
    - with demo mode off: migrations run, the server answers `/health` and `/api/v1/status`, and `docker stop` ends it promptly;
    - with demo mode on (a throwaway database through a scratch override): the lock is acquired, seeds are checked, a second container against the same database waits and then refuses, and `docker stop` during its wait ends it within about a second.

    Results are recorded for T10.

- [ ] **T10. Docs and validation.** Update:
  - `docs/architecture.md`: new §8 "Single process (spec 003d)", a pointer from §2, and the trade-offs moved to §9;
  - `README.md`: run commands, a public-demo summary, lock settings, deploys, `WEB_CONCURRENCY` and the `PORT` row;
  - `AGENTS.md`: the `Run` line;
  - `docs/SPEC.md`: a scope line;
  - `specs/003d-demo-lock/validation.md`: the evidence.

  RF-10, RNF-1, RNF-7
  - Done when:
    - The docs match the code: settings, messages, exit statuses, the order of steps, the RF-9 sum, stop-first deploys, the overlap limit and no transaction pooling.
    - The OpenAPI snapshot test is green.
    - CI is green on a PR (pushed with the owner's confirmation).

## Coverage check

| Req | Tasks |
|---|---|
| RF-1 | T7, T8 (logging), T9 |
| RF-2 | T4, T7 (marker set by `serve`) |
| RF-3 | T2, T7 |
| RF-4 | T3 (step 2), T7, T8 (stop during migrations) |
| RF-5 | T1, T2, T5, T8 |
| RF-6 | T1, T2, T5, T8 |
| RF-7 | T6, T8 |
| RF-7a | T4, T6, T8 |
| RF-8 | T4, T7 |
| RF-9 | T9 |
| RF-10 | T3 (`.env.example`), T10 |
| RNF-1 | T7, T9, T10 (OpenAPI snapshot, unchanged suite) |
| RNF-2 | no migration in any task; `schema_check` stays green |
| RNF-3 | T5 |
| RNF-4 | T8 |
| RNF-5 | T9 (plan §12 approved) |
| RNF-6 | T5, T6, T8 |
| RNF-7 | every task (quality checks) |
