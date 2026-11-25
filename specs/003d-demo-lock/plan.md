# 003d - Demo mode: project start command and single-process lock — technical plan

Status: approved (2026-11-25, including §12: Dockerfile exec-form CMD and 180 s healthcheck start period; `python -m app.serve` in README, AGENTS.md and the container)
Spec: `specs/003d-demo-lock/spec.md` (approved 2026-11-24; no open questions)
Base: branch `feature/003d-demo-lock`, from `master` at `651199a` (003a merged)

## 1. Pre-plan measurements (scratch only, no project files changed)

| Probe | Result | Consequence |
|---|---|---|
| uvicorn 0.42.0 `Config.__init__` | With `workers=None` it reads `WEB_CONCURRENCY` itself (`int(os.environ[...])`), exactly as the CLI did | With demo mode off, the start command passes `workers=None`, so the worker count is today's (RF-1, RNF-1). With demo mode on, it passes `workers=1` explicitly |
| uvicorn 0.42.0 `uvicorn.run` | `workers > 1` or `reload` need an import string; with `workers == 1` and no reload, `server.run()` runs in the calling process. A startup failure exits with status 3 | The start command calls `uvicorn.run("app.main:app", ...)`, so with demo mode on the app is imported in the same process and sees the in-memory marker (RF-2) |
| uvicorn 0.42.0 `Server.capture_signals` | Saves the current SIGINT/SIGTERM handlers, installs its own, restores the saved ones afterwards and re-raises captured signals | The start command resets its handlers to the defaults before calling the server (RF-4 step 7). As PID 1, a re-raised SIGTERM with the default handler is ignored, so the start command then closes the lock and returns 0 |
| psycopg2 2.9.11, bundled libpq 17.6 | `connect_timeout=2` on a blackholed address fails after 2.0 s ("timeout expired"); a closed port fails at once ("Connection refused") | The 2-second connect timeout is real. Each attempt runs in a daemon thread so a stop signal doesn't wait for it (RF-5) |
| SQLAlchemy `make_url(...).render_as_string()` | Encodes a space in `options` as `+`, which libpq does not decode as a space | The lock connection is built from keyword arguments (host, port, user, password, dbname, plus the URL's query parameters), never from a re-rendered URL (D6) |
| `make_url("sqlite://").get_backend_name()` | `sqlite`; `postgresql+psycopg2://...` gives `postgresql` | The PostgreSQL check uses the backend name (RF-4 step 3) |
| PostgreSQL 15.19 (verified in T1, 2026-11-25, scratch script) | `pg_try_advisory_lock(-0x1234567876543210)` appears in `pg_locks` as `classid=3989547399`, `objid=2309737968`, `objsubid=1`, `granted=true`: exactly `(key >> 32) & 0xFFFFFFFF` and `key & 0xFFFFFFFF`. A second session gets `false`. `options="-c tcp_keepalives_idle=10 -c tcp_keepalives_interval=3 -c tcp_keepalives_count=3"` over TCP: `SHOW` returns 10, 3, 3. A keyword `options` replaces the URL's whole `options` (URL `work_mem=8MB` lost, back to `4MB`); in one merged string the later `-c` wins (`idle 99` then `7` gives `7`, `work_mem=8MB` kept). After `pg_terminate_backend`, the next query raises `OperationalError: server closed the connection unexpectedly` | The heartbeat and RF-7a query `pg_locks` by these columns. Keepalive values go in `options`, merged after any `options` from `DATABASE_URL` (§6 `merge_options`), so ours win and the rest are kept |
| libpq 17.6 first-line messages (T1) | Wrong password: `... failed: FATAL:  password authentication failed for user "postgres"` (0.01 s). Unknown role: the same text. Missing database: `... failed: FATAL:  database "nodb" does not exist`. Closed port: `... failed: Connection refused` (0.00 s). Blackholed address: `... failed: timeout expired` (2.0 s). Unknown host: `could not translate host name "nohost.invalid" to address: Name or service not known` | Confirms the §6 `classify_connect_error` list: the first two are `permanent`, the last three are `unreachable` |

## 2. Approach

1. **One start command for every mode**, `python -m app.serve` (module `app/serve.py`). It loads the settings, then:
   - **Demo mode off:** runs the migrations in a child process, then `uvicorn.run` with today's options (`workers=None`, so `WEB_CONCURRENCY` behaves as before).
   - **Demo mode on:** the RF-4 sequence. Check `WEB_CONCURRENCY`, validate every demo setting at once, require PostgreSQL, take the lock, start the heartbeat, run the migrations in a child process, reset the signal handlers, set the start marker, and run the server with the demo options (single worker, no reload).
2. **The lock** lives in `app/services/demo_lock.py`. It runs on a dedicated psycopg2 connection (autocommit, outside SQLAlchemy's pool, RNF-3) and uses a session-level `pg_try_advisory_lock(key)`. A heartbeat thread confirms the key in `pg_locks` on that connection, with a watchdog. On the first failure it runs the "lock lost" callbacks with a bounded wait, flushes the logs and calls `os._exit(1)` (RF-7).
3. **The start marker** and the handle to the lock live in an application module, `app/core/runtime.py`. They don't go in `app/serve.py`, because `python -m` runs that file as `__main__`, a separate module object (spec R4). The lifespan checks the marker first when demo mode is on (RF-2). `runtime.demo_lock_is_held()` is the RF-7a entry point for 003c.
4. **Pure rules** go in `app/services/demo_lock_rules.py`, tested first: `WEB_CONCURRENCY` and `PORT` parsing, the lock key and its split for `pg_locks`, keepalive values from K, merging `options`, connection keyword arguments, classifying connection errors, and removing the password from messages.
5. **Settings:** the five `DEMO_LOCK_*` settings join 003a's demo settings class. `validate_demo_settings()` reports every error of 003a and 003d at once; 003b and 003c add theirs to the same function.
6. **Container:** `CMD ["python", "-m", "app.serve"]` in exec form, and a healthcheck start period of 180 s (RF-9).

## 3. Files

| File | Change | Responsibility | RF |
|---|---|---|---|
| `app/serve.py` | create | The project start command. `main(argv, *, deps) -> int` runs §7.1 and returns the exit status; `if __name__ == "__main__": sys.exit(main())`. `server_options(demo, port) -> dict[str, Any]` and `demo_server_options(port) -> dict[str, Any]`, the single place 003b extends. `run_migrations(database_url, stop, watcher) -> int` runs the Alembic child. The signal handlers set a `threading.Event` | RF-1 to RF-5, RF-8 |
| `app/core/runtime.py` | create | `mark_started_by_serve()`, `started_by_serve() -> bool`, `set_demo_lock(lock)`, `demo_lock_is_held() -> bool` (False when there is no lock). In-memory module state only | RF-2, RF-7a |
| `app/services/demo_lock.py` | create | `DemoLock`: `acquire(config, database_url, *, stop, clock, sleep, connect)` (classmethod, §7.2), `start_heartbeat(on_lost)` (§7.3), `is_held() -> bool` (§7.4), `add_lost_callback(cb)` / `remove_lost_callback(cb)`, `close()`. `LockRefused(message)`, the exception for every refusal. Every query runs in a short-lived daemon worker thread, joined with the heartbeat timeout, and access to the connection is serialized with a `threading.Lock` | RF-5 to RF-7a |
| `app/services/demo_lock_rules.py` | create | The pure functions in §6 | RF-3, RF-5, RF-6, settings |
| `app/core/demo_settings.py` | modify | `RawDemoSettings` gains the five `DEMO_LOCK_*` fields. New `LockConfig` dataclass, `load_lock_config() -> LockConfig` (raises `DemoConfigError` listing every error) and `validate_demo_settings() -> tuple[DemoConfig, LockConfig]`, which runs both loaders and raises one `DemoConfigError` with all their errors | RF-4 step 2, settings |
| `app/main.py` | modify | Lifespan: when demo mode is on, if `not runtime.started_by_serve()`, raise `RuntimeError(START_COMMAND_MESSAGE)` before anything else (before loading the config and seeding). Nothing changes when demo mode is off | RF-2, RF-8 |
| `Dockerfile` | modify | `CMD ["python", "-m", "app.serve"]`. `HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3`, with a comment giving the RF-9 sum | RF-9 |
| `.env.example` | modify | The five `DEMO_LOCK_*` settings, commented, with defaults and valid values | settings, RF-10 |
| `docs/architecture.md` | modify | New §8 "Single process (spec 003d)": the start command and its order, the lock, the key and lock name, the heartbeat and watchdog, the hard exit and its bound, keepalive, exit statuses, settings, the RF-9 sum, PostgreSQL only and no transaction pooling, `WEB_CONCURRENCY`, deploys (stop-first), and the known overlap limit. §2 "Lifecycle" points to it. The trade-offs move to §9 | RF-10 |
| `README.md` | modify | Run commands become `python -m app.serve`, with the auto-reload development variant for demo mode off only. The "Running a public demo" section gains a single-process summary, the lock settings, deploy and `WEB_CONCURRENCY` notes, and an updated warning (003b and 003c still needed). The `PORT` row says the start command reads it | RF-10 |
| `AGENTS.md` | modify | The `Run` command becomes `python -m app.serve`, with the development variant for demo mode off only | RF-10 |
| `docs/SPEC.md` | modify | A current-scope line for 003d, linking to architecture §8 | RF-10 |
| `tests/test_demo_lock_rules.py` | create | Unit tests for §6, written first | RF-3, RF-5, RF-6 |
| `tests/test_demo_settings.py` | modify | Lock settings: defaults, ranges, strict integers, the timeout-below-interval rule naming both variables, the lock name rule, and every error at once together with 003a's errors | settings, RF-4 |
| `tests/test_serve.py` | create | The start command with a stand-in lock, a fake server and a fake migration runner (§10) | RF-1 to RF-4, RF-8 |
| `tests/test_demo_lock.py` | create | `DemoLock` with a fake connection: the retry cadence on an injected clock, wait 0, the stop event, the permanent error, the heartbeat failure, the watchdog on a hanging query, the exit bound with an injected exit function, and `is_held` | RF-5, RF-7, RF-7a |
| `tests/test_demo_startup.py` | modify | The marker check. Existing demo lifespan tests set the marker through a fixture | RF-2 |
| `tests/test_container.py` | create | Static checks of `Dockerfile`: the exec-form `CMD` equals `["python", "-m", "app.serve"]`, and `--start-period=180s` | RF-9 |
| `tests/postgres/test_demo_lock.py` | create | Real PostgreSQL (§10) | RNF-4 |
| `tests/postgres/lock_holder.py` | create | A test entry point (`python -m tests.postgres.lock_holder`) that calls `app.serve.main` with a stand-in server that logs one INFO line and sleeps, so lock, heartbeat, signal and exit behaviour run in a real separate process without starting checks or touching the network | RNF-4 |

No new dependency (psycopg2 and uvicorn are already in `requirements.txt`). No migration (RNF-2). `docker-compose.yml` is unchanged, because it builds the image and inherits the new `CMD`.

## 4. Configuration and state

### 4.1 Settings (read only while demo mode is on; strict decimal integers as in 003a)

| Variable | Default | Valid | Error text (prefix `<VAR>: `) |
|---|---|---|---|
| `DEMO_LOCK_WAIT_SECONDS` | 60 | 0–3600 | 003a's `parse_strict_int` messages |
| `DEMO_LOCK_HEARTBEAT_SECONDS` | 10 | 1–300 | same |
| `DEMO_LOCK_TIMEOUT_SECONDS` | 5 | 1–60, and `< DEMO_LOCK_HEARTBEAT_SECONDS` | `DEMO_LOCK_TIMEOUT_SECONDS (5) must be less than DEMO_LOCK_HEARTBEAT_SECONDS (5)` |
| `DEMO_LOCK_KEEPALIVE_SECONDS` | 30 | 10–600 | same as above |
| `DEMO_LOCK_NAME` | `service-monitoring-api-demo` | `^[A-Za-z0-9_-]{1,100}$` (ASCII letters) | `DEMO_LOCK_NAME: must be 1 to 100 characters of letters, digits, '-' and '_'` |

Server variables, read from the process environment by the start command only, as uvicorn's CLI and the container's shell did before (not from `.env`, D8):

| Variable | Default | Rule |
|---|---|---|
| `WEB_CONCURRENCY` | 1 when unset or empty | Demo mode on: decimal digits, at least 1, else `WEB_CONCURRENCY: must be a whole number of at least 1`. Above 1: `Demo mode requires a single process (WEB_CONCURRENCY=<n>)`. Demo mode off: not read by the start command; uvicorn reads it as today |
| `PORT` | 8000 | Decimal digits, 1–65535, else `PORT: must be a whole number from 1 to 65535`, in every mode |

### 4.2 Protocol constants (`app/services/demo_lock.py`, not settings; spec M11)

| Constant | Value | Use |
|---|---|---|
| `RETRY_SECONDS` | 1.0 | Lock retry cadence (RF-5) |
| `CONNECT_TIMEOUT_SECONDS` | 2 | libpq `connect_timeout` (RF-5, R16) |
| `STOP_POLL_SECONDS` | 0.1 | How often the waits check the stop event (keeps the stop response under about one second) |
| `CALLBACK_BUDGET_SECONDS` | 1.0 | Total wait for the "lock lost" callbacks before the hard exit (RF-7 bound) |
| `KEEPALIVE_PROBES` | 3 | `tcp_keepalives_count` (RF-6) |

### 4.3 Runtime state (in memory)
- `app.core.runtime`: `_started_by_serve: bool = False` and `_demo_lock: DemoLock | None = None`.
- `DemoLock`: the connection, the key, `_conn_lock: threading.Lock`, `_callbacks: list[Callable[[], None]]`, and the heartbeat thread (a daemon thread with its own stop event).
- No stored state (RNF-2).

## 5. Messages and exit statuses (exact)

| Situation | Logged at ERROR by `app.serve` | Exit |
|---|---|---|
| Invalid settings (pydantic, for example a missing `DATABASE_URL` or an invalid `DEMO_MODE`) | `Invalid settings:` followed by one line per error (pydantic `loc` and `msg`, never `input`) | 1 |
| `WEB_CONCURRENCY` invalid / above 1 | see §4.1 | 1 |
| Invalid demo settings | 003a's `Invalid demo settings:` block, with every 003a and 003d error | 1 |
| Not PostgreSQL | `Demo mode requires PostgreSQL` | 1 |
| Permanent connection error | `Demo mode could not connect to the database: <first line of the libpq message, password removed>` | 1 |
| Wait timed out, database reached at least once | `Demo mode requires a single process: another process holds the demo lock` | 1 |
| Wait timed out, database never reached | `Demo mode could not reach the database to take the demo lock` | 1 |
| Migrations failed | `Migrations failed (exit status <n>)` | 1 |
| Stop signal while waiting or during the migrations | INFO `Stop signal received; exiting` | 0 |
| Lock lost (heartbeat) | `Demo lock lost (<reason>); stopping demo jobs and exiting`, where `<reason>` is `heartbeat failed: <error class>`, `heartbeat timed out after <t> s` or `lock no longer held` | 1 (hard exit) |
| Started any other way with demo mode on (lifespan) | `RuntimeError("Demo mode must be started with the project start command (python -m app.serve)")`; uvicorn reports the startup failure | 3 (uvicorn) |
| Server returns | — | 0, or uvicorn's own status |

INFO lines on success: `Demo lock acquired (name <name>, database <db>)`, `Running migrations`, `Starting server (demo mode on|off)`.

## 6. Pure logic functions (`app/services/demo_lock_rules.py`)

| Function | Signature | Rule |
|---|---|---|
| `parse_web_concurrency` | `(raw: str \| None) -> int` | Unset or `""` → 1. Otherwise the 003a strict-digits rule with low 1 and no upper bound; raises `ValueError` with the §4.1 text |
| `parse_port` | `(raw: str \| None) -> int` | Unset or `""` → 8000; strict digits 1–65535 |
| `lock_key` | `(name: str, database: str) -> int` | `int.from_bytes(sha256(f"{name}\0{database}".encode()).digest()[:8], "big", signed=True)`, a signed 64-bit value for `pg_try_advisory_lock(bigint)`. Deterministic; different names or databases give different keys |
| `split_key` | `(key: int) -> tuple[int, int]` | `((key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF)`, the `classid` and `objid` in `pg_locks` |
| `keepalive_settings` | `(k: int) -> tuple[int, int, int]` | `(max(1, k // 3), max(1, k // 9), 3)`. Detection time `idle + 3 × interval ≤ k/3 + k/3 < k` for every k in 10–600 (RF-6) |
| `merge_options` | `(existing: str \| None, ours: dict[str, int]) -> str` | `existing` followed by ` -c name=value` for each of ours, so ours take precedence (later `-c` wins) |
| `connect_kwargs` | `(database_url: str, keepalive_k: int) -> dict[str, Any]` | From `make_url`: `host`, `port`, `user`, `password`, `dbname` (omitting the ones that are `None`), every query parameter, then the lock options, which win: `connect_timeout=2`, `application_name="service-monitoring-api-demo-lock"`, `options=merge_options(...)` |
| `lock_database` | `(database_url: str) -> str` | `url.database`, or `url.username` when the database is omitted (libpq's default), for the key and the log line |
| `classify_connect_error` | `(message: str) -> Literal["unreachable", "permanent"]` | `unreachable` if the message contains one of: `Connection refused`, `timeout expired`, `could not translate host name`, `Name or service not known`, `Temporary failure in name resolution`, `No route to host`, `Network is unreachable`, `the database system is starting up`, `the database system is shutting down`, `the database system is in recovery mode`, `sorry, too many clients already`, `server closed the connection unexpectedly`, `No such file or directory` (socket not yet there). Everything else is `permanent` (wrong password, missing database or role, `pg_hba.conf`, SSL, protocol). Confirmed on PostgreSQL 15 in T1 (§1) |
| `public_reason` | `(message: str, password: str \| None) -> str` | The first line, stripped, with every occurrence of the password (raw and percent-encoded) replaced by `***` |

## 7. Algorithms (pseudocode)

### 7.1 Start command (RF-1 to RF-5, RF-8)
```
main(deps):                                   # deps: lock_factory, run_server, run_migrations, clock, sleep, exit
  configure logging (same format as app.main, INFO)
  stop = Event(); install SIGTERM/SIGINT handlers -> stop.set()        # every mode (D9)
  try: settings = Settings()                  # .env included (RF-1); pydantic errors -> §5, exit 1
  port = parse_port(env PORT)                 # §4.1
  if not settings.demo_mode:                  # RF-8: no lock, no WEB_CONCURRENCY, no PostgreSQL, no marker check
      status = run_migrations(settings.DATABASE_URL, stop, lock=None)
      if stop.is_set(): return 0
      if status != 0: error; return 1
      return serve(server_options(demo=False, port))
  workers = parse_web_concurrency(env WEB_CONCURRENCY)                 # RF-4.1, RF-3 (first demo-mode check)
  validate_demo_settings()  -> lock_config    # RF-4.2, all errors at once
  if backend(settings.DATABASE_URL) != "postgresql": refuse "Demo mode requires PostgreSQL"   # RF-4.3
  lock = lock_factory.acquire(lock_config, settings.DATABASE_URL, stop=stop)   # RF-4.4, §7.2; None if stopped
  if lock is None: return 0
  runtime.set_demo_lock(lock)
  lock.add_lost_callback(stop_demo_jobs)      # scheduler shutdown (003c registers its jobs too)
  lock.start_heartbeat()                      # RF-4.5, §7.3
  status = run_migrations(settings.DATABASE_URL, stop, lock)          # RF-4.6, §7.5
  if stop.is_set(): lock.close(); return 0
  if status != 0: lock.close(); error; return 1
  return serve(demo_server_options(port), lock)

serve(options, lock=None):                    # RF-4.7
  signal(SIGTERM, SIG_DFL); signal(SIGINT, default_int_handler)       # the server owns stop signals now
  runtime.mark_started_by_serve()             # in memory, same process, just before the server
  try: run_server("app.main:app", **options)  # uvicorn.run; startup failure -> SystemExit(3) propagates
  finally: if lock: lock.close()
  return 0

server_options(demo, port):
  demo -> demo_server_options(port)
  else -> {host: "0.0.0.0", port, workers: None, reload: False}      # workers None: uvicorn reads WEB_CONCURRENCY
demo_server_options(port) -> {host: "0.0.0.0", port, workers: 1, reload: False}   # 003b adds its options here
```
"Before anything else" in RF-3 is read as the first demo-mode step: the settings must be loaded to know whether demo mode is on, and loading them has no side effects.

### 7.2 Taking the lock (RF-5)
```
acquire(config, url, stop, clock, sleep, connect):
  key = lock_key(config.name, lock_database(url)); kwargs = connect_kwargs(url, config.keepalive)
  deadline = clock() + config.wait; reached = False; conn = None
  loop:
    if conn is None:
      result = attempt_in_thread(connect, kwargs)                     # daemon thread
      wait for result, checking stop every STOP_POLL_SECONDS          # a stop abandons the attempt
      if stop: return None
      if error: if classify(error) == "permanent": refuse "could not connect: " + public_reason
               (unreachable: fall through to the retry wait)
      else: conn = result; conn.autocommit = True; reached = True
    if conn: try got = query(conn, "SELECT pg_try_advisory_lock(%s)", key)    # in a worker thread, bounded
             except error: close conn; conn = None; got = False
             if got: log acquired; return DemoLock(conn, key, config)
    if clock() >= deadline: close conn; refuse (reached ? "another process holds..." : "could not reach...")
    wait until min(next attempt (1 s after this attempt began), deadline) or stop   # stop -> return None
```
Wait 0 gives exactly one attempt, because the deadline has already passed after it. Unix-socket URLs work unchanged (libpq `host=/path`).

### 7.3 Heartbeat, watchdog and hard exit (RF-7)
```
heartbeat thread (daemon):
  while not hb_stop.wait(config.heartbeat):
    started = clock()
    ok, reason = bounded_query(HELD_SQL, timeout=config.timeout)      # worker thread + join(timeout)
    if not ok: lost(reason)                                           # error, timeout or not held

HELD_SQL = SELECT EXISTS (SELECT 1 FROM pg_locks
            WHERE locktype = 'advisory' AND granted AND pid = pg_backend_pid()
              AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND classid = %(classid)s AND objid = %(objid)s AND objsubid = 1)

bounded_query(sql, timeout):
  if not conn_lock.acquire(timeout=timeout): return (False, "heartbeat timed out after <timeout> s")
  run sql in a daemon worker thread; join(remaining time)
  still running -> leave conn_lock held (the connection is unusable) and return timed out
  raised -> (False, "heartbeat failed: <class>"); False -> (False, "lock no longer held"); True -> (True, "")

lost(reason):
  log ERROR (§5)
  run the callbacks in one daemon thread, join(CALLBACK_BUDGET_SECONDS)   # stop demo jobs; kill the migration child
  flush logging handlers
  exit(1)                                     # os._exit: no graceful drain (R1)
```
The bound is the heartbeat timeout, plus at most 1 s of callbacks, plus the flush and exit. That is under the timeout plus 2 s from the failed heartbeat's start, as RF-7 requires. A reset in progress is rolled back by PostgreSQL when the process dies (003c RF-8).

### 7.4 Lock status for other parts (RF-7a)
```
DemoLock.is_held(): ok, _ = bounded_query(HELD_SQL, timeout=config.timeout); return ok
runtime.demo_lock_is_held(): return _demo_lock is not None and _demo_lock.is_held()
```
It is a fresh query every time, and any error or timeout counts as not held. It doesn't trigger `lost()`: the heartbeat alone decides to exit.

### 7.5 Migrations child (RF-1, RF-4 step 6)
```
run_migrations(database_url, stop, lock):
  child = Popen([sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=PROJECT_ROOT, env={**os.environ, "DATABASE_URL": database_url})
  if lock: lock.add_lost_callback(kill_child)
  try:
    loop: if child.poll() is not None: return child.returncode
          if stop.wait(STOP_POLL_SECONDS): child.terminate(); wait 0.5 s; kill if alive; return -1
  finally: if lock: lock.remove_lost_callback(kill_child)
```
`migrations/env.py` calls `load_dotenv()`, which never overrides a variable that is already set, so the child uses the `DATABASE_URL` the settings resolved. Alembic's `fileConfig` runs in the child only, so the server's logging is untouched (R11).

### 7.6 Application startup (RF-2)
```
lifespan:
  if settings.demo_mode:
      if not runtime.started_by_serve(): raise RuntimeError(START_COMMAND_MESSAGE)   # before 003a's steps
      ... 003a steps unchanged ...
```
Workers spawned by uvicorn's reloader or multiprocess supervisor start a fresh interpreter, so their `runtime` module has the flag unset.

## 8. Rendering / UI behavior
- No endpoint, response or schema change. The OpenAPI snapshot test stays green (RNF-1).
- Logs: the §5 INFO and ERROR lines. No password ever appears (§6 `public_reason`).
- Container: `docker stop` reaches the start command directly (exec-form `CMD`).

## 9. Technical decisions

| ID | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | Session-level `pg_try_advisory_lock` on a dedicated autocommit psycopg2 connection | A lock row in a table; a SQLAlchemy pooled connection | The lock disappears with the connection (RF-6), there is no schema (RNF-2), and a dedicated connection keeps the pool's capacity (RNF-3) |
| D2 | `uvicorn.run(...)` in the start command's own process | Running `uvicorn` as a child process | The marker must be in the same process (RF-2, R4). A child would also make the start command a signal forwarder |
| D3 | Every query and connect attempt runs in a short-lived daemon thread, joined with a timeout | `statement_timeout`; asynchronous psycopg2 | A blocking call on a dead network ignores statement timeouts (spec R2). A daemon thread can simply be abandoned, and the process exits anyway when the lock is lost |
| D4 | `os._exit(1)` after the callbacks, with a 1 s budget | Asking uvicorn to stop (`should_exit`) | uvicorn's graceful stop waits for connections and exits 0, so it can't meet the bound (spec R1) |
| D5 | Lock key from SHA-256 of `name\0database` | `hashtext()` in SQL; crc32 | A stable 64-bit value computed in Python, so it is known before connecting and testable as a pure function. crc32 is only 32 bits |
| D6 | Connection keyword arguments built from `make_url`, with lock options winning and `options` merged | Re-rendering the URL with SQLAlchemy | The rendered URL encodes spaces as `+`, which libpq doesn't decode as a space (§1). Keyword arguments keep the query parameters and make precedence explicit (R14) |
| D7 | Marker and lock handle in `app/core/runtime.py` | An environment variable; a variable in `app/serve.py` | Children inherit environment variables; `python -m app.serve` runs as `__main__`, so `app.serve` would be a second, unset module object (spec R4) |
| D8 | `WEB_CONCURRENCY` and `PORT` read from the process environment only | Adding them to `Settings` (which also reads `.env`) | Today they come from the environment only (uvicorn CLI and the container shell). Reading `.env` would change behaviour with demo mode off (RNF-1). They aren't application settings (spec M8) |
| D9 | The start command installs its stop handlers in every mode, until the server starts | Handlers only in demo mode | As PID 1 in the container, a process without a handler ignores SIGTERM. Today the `sh -c` shell ignored it too during migrations; handling it costs nothing and makes `docker stop` prompt in both modes |
| D10 | A test entry point (`tests/postgres/lock_holder.py`) with a stand-in server for the process-level PostgreSQL tests | Starting the real server in those tests | Seeding and the first checks would make network requests. The stand-in still runs the real start command, lock, heartbeat, signals, migrations and exit paths in a separate process |
| D11 | A static test of `Dockerfile` plus a manual compose run | Building the image in CI | CI has no Docker build step today. The static test guards the two values the spec fixes |

## 10. Test strategy

Tests are written first for the pure functions and for `DemoLock` with fakes. Time-based behaviour uses an injected clock and sleep, or short configured values, never the defaults (RNF-6).

| Req | Default suite (SQLite, no network) | PostgreSQL (`postgres` marker) |
|---|---|---|
| RF-1 | `test_serve.py`: demo mode off runs the migrations child with the resolved `DATABASE_URL` in its environment, then the server with `{host 0.0.0.0, port, workers None, reload False}`; `PORT` parsing; `.env` honoured (a temporary `.env` in a temporary working directory); the migration command is `[sys.executable, "-m", "alembic", "upgrade", "head"]` | `test_logging_stays_info_after_migrations`: the holder process runs the real migrations on a temporary database, and its INFO line from the stand-in server appears in its output |
| RF-2 | `test_demo_startup.py`: demo lifespan without the marker → the exact `RuntimeError`, before `load_demo_config` is called; with the marker → starts. `test_serve.py`: `serve` sets the marker before calling the server | — |
| RF-3 | `test_demo_lock_rules.py`: unset, empty, `1`, `2`, `0`, `+1`, `1.0`, ` 1`, `٣`. `test_serve.py`: `WEB_CONCURRENCY=2` refuses with the exact text before the settings are validated and before any lock attempt | — |
| RF-4 | `test_serve.py` with recording fakes: the order of steps is exactly WEB_CONCURRENCY → settings → PostgreSQL → lock → heartbeat → migrations → handlers reset → marker → server; each failure stops the later steps and returns 1; the signal handlers are the defaults when the fake server is called; every 003a and 003d settings error appears in one message; a `sqlite://` URL refuses with the exact text | `test_stop_during_migrations`: the holder runs a slow stand-in migration child; SIGTERM → exit 0 within 1.5 s and the child is gone |
| RF-5 | `test_demo_lock.py` with a fake connector and clock: held → retried every 1 s until the wait ends; wait 0 → one attempt; unreachable → retried, then the "could not reach" text; reached then held → the "another process" text; permanent → refused at once, no retry, password removed; the stop event during a hanging connect → returns `None` within 0.3 s | `test_second_process_waits_then_refuses` (short wait), `test_second_process_takes_over_when_first_stops`, `test_wait_zero_is_one_attempt`, `test_stop_signal_during_wait_exits_0_within_a_second` (a real subprocess), `test_wrong_password_refused_at_once` and `test_missing_database_refused_at_once` (no password in output) |
| RF-6 | `test_demo_lock_rules.py`: `keepalive_settings` for k = 10, 30, 600, with detection below k for every k in 10–600; `merge_options` precedence; `connect_kwargs` keeps the query parameters and the lock options win | `test_keepalive_applied_to_lock_session` (`SHOW tcp_keepalives_*` on the lock connection equals `keepalive_settings(30)`); `test_released_when_holder_killed` (SIGKILL the holder, a second takes the lock at once); `test_released_on_clean_stop` |
| RF-7 | `test_demo_lock.py`: a failed, timed-out or not-held heartbeat → the callbacks run, then the injected exit gets 1, within timeout + 2 s (timeout 1 s); a callback that hangs doesn't delay past the budget; the migration child is killed on loss | `test_holder_exits_1_when_lock_connection_terminated` (`pg_terminate_backend` on the holder's lock connection, exit status 1 within interval + timeout + 2 s, see the note below); `test_watchdog_ends_hanging_heartbeat` (the holder's heartbeat query swapped for `SELECT pg_sleep(30)` through a test hook → exit 1 within the bound); `test_restart_retakes_lock` |
| RF-7a | `test_demo_lock.py`: `is_held` true, false on error, false on a hang after the timeout, doesn't call `lost`; `runtime.demo_lock_is_held()` is false without a lock | `test_is_held_reflects_the_database` (true, then false after `pg_terminate_backend`) |
| RF-8 | `test_serve.py`: demo mode off with `WEB_CONCURRENCY=3`, a `sqlite://` URL and no marker → no lock factory call, no refusal, server options `workers None`. `test_demo_startup.py`: lifespan with demo mode off ignores the marker | — |
| RF-9 | `test_container.py`: exec-form `CMD`, `--start-period=180s` | Manual: `docker compose up --build` with demo mode on and off; `docker stop` timing; healthcheck becomes healthy |
| RF-10 | — | Docs checked by `/sdd-validate` against the code; the `.env.example` test (spec 000) is updated for the new variables |
| RNF-1 | Full existing suite unchanged; OpenAPI snapshot test | Manual compose run with demo mode off |
| RNF-2, RNF-3 | `schema_check` unchanged (no migration); the lock uses `psycopg2.connect`, not the engine (asserted in `test_demo_lock.py` by the injected connector) | — |

Note on RF-7 timing: in the PostgreSQL tests the holder runs with `DEMO_LOCK_HEARTBEAT_SECONDS=2` and `DEMO_LOCK_TIMEOUT_SECONDS=1`, the shortest values the settings allow, so the bound is 2 + 1 + 2 = 5 s.

## 11. Risks

| Risk | Mitigation |
|---|---|
| The PostgreSQL facts in §1 were taken from the documentation, because Docker was unreachable while writing | Verified in T1 on PostgreSQL 15.19 (§1); no change to the design was needed |
| As PID 1, between resetting the handlers and uvicorn installing its own, a SIGTERM is ignored | The window is a few milliseconds. Docker's grace period still ends the process, and the lock is released with the connection |
| `os._exit` skips `atexit` and buffered output | Log handlers are flushed explicitly before the exit; nothing else needs `atexit` |
| A hung worker thread keeps the connection lock | That only happens when the connection is already unusable, and the heartbeat then exits the process |
| A key collision with 003a's cap lock (crc32 used as a bigint) | The probability is negligible (a 64-bit hash against one fixed 32-bit value); the keys are also used in different lock modes (session versus transaction) |
| Platforms that start the new instance and wait for it to be healthy before stopping the old one | Documented: they need a stop-first strategy, otherwise the new process refuses after the wait (RF-10) |
| Subprocess-based PostgreSQL tests are slower and timing-sensitive | Short settings, generous assertion margins (for example "within 1.5 s" for "about one second"), and a clear failure message with the measured time |

## 12. Approvals needed
1. **Container:** `Dockerfile` `CMD` changes from `sh -c "alembic upgrade head && uvicorn ..."` to exec-form `["python", "-m", "app.serve"]`, and the healthcheck start period from 15 s to 180 s (RNF-5; direction approved 2026-11-24).
2. **Start command:** the README, AGENTS.md and the container all use `python -m app.serve`. AGENTS.md is a project instruction file, so its `Run` line changes too.

No new dependency, no migration, no file deletion, no `docker-compose.yml` change.

## 13. RF coverage matrix

| Req | Plan sections |
|---|---|
| RF-1 | §2.1, §3 `serve.py`, §4.1 `PORT`, §7.1, §7.5, D2, D8 |
| RF-2 | §3 `runtime.py` and `main.py`, §7.1 `serve`, §7.6, D7 |
| RF-3 | §4.1, §6 `parse_web_concurrency`, §7.1 |
| RF-4 | §5, §7.1, §7.5, D9 |
| RF-5 | §4.2, §5, §6 `lock_key`, `connect_kwargs`, `classify_connect_error`, `public_reason`, §7.2, D3, D5, D6 |
| RF-6 | §6 `keepalive_settings`, `merge_options`, §7.2, D1 |
| RF-7 | §4.2, §5, §7.3, D3, D4 |
| RF-7a | §3 `runtime.py`, §7.4 |
| RF-8 | §7.1 demo-off branch, §7.6 |
| RF-9 | §3 `Dockerfile`, §12, D11 |
| RF-10 | §3 docs rows |
| RNF-1 | §1 (uvicorn `workers=None`), D8, §10 |
| RNF-2 | D1, §12 (no migration) |
| RNF-3 | D1, §10 |
| RNF-4 | §3 PostgreSQL test files, §10, D10 |
| RNF-5 | §12 |
| RNF-6 | §7.2 injected clock, §10 |
| RNF-7 | §10 (existing gates) |
