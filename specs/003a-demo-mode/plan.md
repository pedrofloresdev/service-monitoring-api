# 003a - Demo mode: seeding, protection and caps — technical plan

Status: approved (2026-11-24, including §12: new `services.origin` column via one Alembic revision)
Spec: `specs/003a-demo-mode/spec.md` (approved 2026-11-24; no open questions)
Base: branch `feature/003-demo-mode`

## 1. Pre-plan measurements (scratch only, no project files changed)

| Probe | Result | Consequence |
|---|---|---|
| `HttpUrl` normalization (pydantic 2.12) | `http://Example.COM` → `http://example.com/`; `https://bücher.de:8443/p` → `https://xn--bcher-kva.de:8443/p`; `http://[2001:DB8::1]:80/` → host `[2001:db8::1]`, default port dropped; `http://example.com./x` keeps the trailing dot | The normalized URL is `str(HttpUrl(raw))`. The cap host is the parser's host with the trailing dot stripped and the brackets removed; it is already lower-case and punycode (RF-15, Definitions) |
| Per-field `mode="before"` validators on `ServiceUpdate` (scratch copy) | `{"url": null, "is_active": null}` → two errors `null_not_allowed`, messages `url cannot be null`, `is_active cannot be null`. OpenAPI JSON **byte-identical** to before | RF-19 is done with validators, with no schema change (RNF-1) |
| APScheduler 3.11 `add_job` | Accepts `next_run_time` and `misfire_grace_time`. `misfire_grace_time=None` means "run no matter how late" (documented in `base.py`) | RF-8: a one-off first-check job with `next_run_time=now` and `misfire_grace_time=None`. The regular interval jobs are unchanged (RNF-1) |
| Spec 002's handler-level 422 (`app/main.py:34`) | A domain exception plus a JSON handler produce the validation-error shape | RF-13 reuses this pattern |
| Current handlers (`app/api/v1/services.py`) | Create checks duplicates by exact text, then commits, then schedules. Update applies `setattr` before committing, with no rollback path. Delete unschedules before committing | The flows move into a service module (§3) that checks everything before mutating, and schedules only after a successful commit (RF-16) |
| Current scheduler | One module-level `BackgroundScheduler()` with defaults: 10 threads, misfire grace 1 s | Matches the spec's assumption ("10 check workers"). Only the seeded first checks get the exemption |

## 2. Approach

1. **Settings** (RF-1 to RF-3):
   - `DEMO_MODE` is parsed strictly at import, so an invalid value fails at startup.
   - The other demo settings live in a separate settings class that is built only when demo mode is on.
   - All parsing rules are pure functions.
2. **Origin** (RNF-2): a new `origin` column (`seeded`, `visitor`, `ordinary`) added by one Alembic revision. Existing rows get `ordinary`.
3. **Seeding** (RF-4 to RF-8):
   - At startup, seeds are validated (spec 002 URL rules, with hostnames resolved concurrently).
   - A pure planner compares the stored services with the seed list and produces actions.
   - Applying the plan and demoting removed seeds happen in one transaction, before the scheduler starts.
   - Seeded services get an immediate first check that is exempt from the misfire limit.
4. **Write rules** (RF-10 to RF-16, RF-19): create, update and delete move into `app/services/service_writes.py`. That module:
   - runs the checks in the RF-16 order, without mutating anything;
   - then runs one atomic step: a cap lock in the database, cap re-checks, the change, and the commit;
   - maps uniqueness races to 409;
   - schedules only after a successful commit.

   The routes stay thin.
5. **Docs** (RF-17): the architecture doc, the product SPEC scope, the README section and `.env.example`.

## 3. Files

| File | Action | Responsibility | RF / RNF |
|---|---|---|---|
| `app/core/config.py` | modify | Add `DEMO_MODE: str \| None` and a `demo_mode: bool` value computed by `parse_demo_flag` at construction. An invalid value raises at import, so startup fails naming `DEMO_MODE` | RF-1, RNF-1 |
| `app/core/demo_settings.py` | create | `DemoSettings` (pydantic-settings, same `.env`, every field read as a raw string) and `load_demo_config() -> DemoConfig`. It parses with the pure functions, collects every error, and raises `DemoConfigError` listing them all. It is never called while demo mode is off | RF-2, RF-3 |
| `app/services/demo_rules.py` | create | **Pure functions** (§6): flag and integer parsing, seed-list parsing, normalized URL, cap host, the seed planner, and the cap decisions | RF-1 to RF-6, RF-15 |
| `app/services/demo_seeding.py` | create | `validate_seed_urls(config)`: spec 002 checks, with hostnames resolved concurrently in a thread pool. `apply_seed_plan(session, config) -> list[int]`: applies the planner's actions in the caller's transaction, without committing, and returns the seeded ids that need a first check | RF-4, RF-5, RF-6, RF-9 |
| `app/services/service_writes.py` | create | `create_service`, `update_service`, `delete_service` flows (§7.2–§7.4), domain errors (`ServiceNotFound`, `ServiceProtected`, `DemoIntervalTooLow`, `DemoCapReached`, `DuplicateUrl`), the `cap_guard` context manager and duplicate detection | RF-10 to RF-16 |
| `app/api/v1/services.py` | modify | Routes call `service_writes` and map the domain errors to HTTP. `get_service` and `list_services` are unchanged | RF-10 to RF-16 |
| `app/main.py` | modify | Lifespan, while demo mode is on: `load_demo_config()`, `validate_seed_urls`, `apply_seed_plan` committed in one transaction (failure stops startup), then `start_scheduler(first_check_ids=…)`. Exception handlers for `DemoIntervalTooLow` (422 shape) and the 403/409 domain errors | RF-3, RF-4, RF-7, RF-8, RF-13 |
| `app/schemas/services.py` | modify | `ServiceUpdate`: a `mode="before"` validator on `name`, `url`, `expected_status`, `check_interval` and `is_active` that raises `null_not_allowed` / `<field> cannot be null` | RF-19 |
| `app/workers/scheduler.py` | modify | `start_scheduler(first_check_ids: Sequence[int] = ())` adds, for each id, a one-off job `first_check_<id>` (`next_run_time=now`, `misfire_grace_time=None`). It also exposes `schedule_first_check(service_id)` for 003c. Interval jobs are unchanged | RF-8, RNF-1 |
| `app/db/models/services.py` | modify | `ServiceOrigin(StrEnum)` with `seeded`, `visitor` and `ordinary`. Column `origin: Mapped[str]`, `String(8)`, NOT NULL, default `ordinary`, server default `'ordinary'`. `CheckConstraint("origin IN (...)", name="ck_services_origin")` | RNF-2 |
| `migrations/versions/<rev>_add_service_origin.py` | create (via `alembic revision`) | Add the column with server default `'ordinary'`, which fills existing rows, plus the check constraint. The downgrade drops both | RNF-2 |
| `.env.example` | modify | Add `DEMO_MODE=false` and the four other demo settings, commented, with defaults and a one-seed example. This keeps spec 000's "lists exactly the settings the app reads" true | RF-17 |
| `docs/architecture.md` | modify | New §7 "Demo mode": settings and seed format, origins, seeding and first checks, protection, caps and cap host, the error order, the RF-14 and RF-19 changes, edge cases, and the need for 003b to 003d | RF-17 |
| `docs/SPEC.md` | modify | Current-scope line summarizing 003a, with a link to architecture §7. The roadmap was already renumbered at approval | RF-17 |
| `README.md` | modify | New "Running a public demo" section: how to enable demo mode, the settings table, and a warning that 003b to 003d are required for public use | RF-17 |
| `tests/test_demo_rules.py` | create | Unit tests for every pure function, written first | RF-1 to RF-6, RF-15 |
| `tests/test_demo_startup.py` | create | Settings loading and refusals, `DEMO_MODE` cases, ignoring malformed settings while off, seed validation errors, seeding at startup with a real scheduler | RF-1 to RF-9, RNF-4 |
| `tests/test_demo_writes.py` | create | API tests with demo mode on: 403, RF-12, RF-13, caps, RF-16 order, rejections leaving state unchanged | RF-10 to RF-16 |
| `tests/test_service_writes.py` | create | Demo mode off: RF-14 (legacy rows, own URL, non-URL database errors) and RF-19. Existing behavior is unchanged | RF-14, RF-19, RNF-1 |
| `tests/conftest.py` | modify | A `demo_config` fixture that turns demo mode on for one test (it overrides the module-level config) and turns it off again afterwards | none (test infrastructure) |
| `tests/postgres/test_demo_concurrency.py` | create | Concurrent creates at each cap boundary, concurrent host-changing updates, concurrent duplicate creates and updates (the uniqueness-race path), and the migration on a seeded database | RNF-3 |
| `MEMORY.md` | modify | End of each phase | none (process) |

**Not touched:** `Dockerfile`, compose, `requirements*.txt` (no new dependency), the existing migrations, `app/services/monitor.py`, `app/services/status.py` and the response schemas (`ServiceResponse` has no `origin`).

## 4. Configuration and state

### 4.1 Settings (RF-1 to RF-3)

| Variable | Read when | Parsing | Error naming |
|---|---|---|---|
| `DEMO_MODE` | always, at import | `parse_demo_flag`: unset → off. Accepted values, case-insensitive: `true`, `1`, `yes`, `on` → on; `false`, `0`, `no`, `off` → off. Anything else, including empty, is an error | `DEMO_MODE` |
| `DEMO_SEED_SERVICES` | only while on | `parse_seed_list` (§6) | `DEMO_SEED_SERVICES`, plus the whole-list problem or a 1-based position and the name |
| `DEMO_GLOBAL_VISITOR_CAP` | only while on | `parse_strict_int(1, 1000)`, default 20 | the variable |
| `DEMO_PER_HOST_VISITOR_CAP` | only while on | `parse_strict_int(1, …)`, default 3, then the rule per-host ≤ global | both variables for the cap rule |
| `DEMO_MIN_CHECK_INTERVAL_SECONDS` | only while on | `parse_strict_int(10, 86400)`, default 300 | the variable |

`DemoConfig` is a frozen dataclass: `seeds: tuple[SeedDefinition, ...]`, `global_cap`, `per_host_cap`, `min_interval`. It is stored once at startup in `app.core.demo_settings.demo_config`, which is `None` when demo mode is off. Request handlers read it from there, and tests swap it through the fixture.

### 4.2 Stored state (RNF-2)

| Column | Type | Default | Constraint |
|---|---|---|---|
| `services.origin` | `VARCHAR(8)` NOT NULL | server default `'ordinary'` (also the Python default) | `ck_services_origin`: `origin IN ('seeded','visitor','ordinary')` |

The column has no index: the largest count query covers at most about 1,000 visitor rows. The spec 001 schema check compares columns, types, nullability and defaults, so the model and the migration must match exactly. The `postgres` test `test_full_history_matches_models` verifies this.

## 5. Error responses (exact)

| Case | Status | Body |
|---|---|---|
| RF-10 seeded | 403 | `{"detail": "Seeded demo services cannot be changed."}` |
| RF-11 ordinary | 403 | `{"detail": "This service cannot be changed while demo mode is on."}` |
| RF-13 interval | 422 | `{"detail": [{"type": "demo_interval_too_low", "loc": ["body","check_interval"], "msg": "In demo mode the check interval must be at least <M> seconds", "input": <value>}]}` |
| RF-14 duplicate | 409 | `{"detail": "A service with this URL already exists"}` |
| RF-15 global | 409 | `{"detail": "Demo limit reached: at most <G> visitor services at a time."}` |
| RF-15 per host | 409 | `{"detail": "Demo limit reached: at most <H> visitor services for host <host>."}` |
| RF-19 null | 422 | FastAPI validation list: one entry per null field, `type: null_not_allowed`, `msg: <field> cannot be null` |

## 6. Pure logic functions (`app/services/demo_rules.py`)

| Function | Signature (intent) | Rule |
|---|---|---|
| `parse_demo_flag` | `(raw: str \| None) -> bool` | As in §4.1. Raises `ValueError` for anything else |
| `parse_strict_int` | `(raw: str \| None, default: int, low: int, high: int) -> int` | `None` gives the default. Otherwise the value must match `^[0-9]+$` and fall within `[low, high]` |
| `parse_seed_list` | `(raw: str \| None) -> tuple[list[SeedDefinition], list[str]]` | Checks, in order: JSON, then an array, then 1 to 20 entries, then each entry. An entry needs `name` (str, 1–100) and `url` (str, accepted by the `ServiceCreate.url` type, with no user information). `expected_status` and `check_interval` must be JSON integers (`type(v) is int`, which rejects `true`) within the API ranges, and default to the `ServiceCreate` field defaults. Unknown keys are errors. Same normalized URL twice is an error. Errors are collected, not raised |
| `normalized_url` | `(raw: str) -> str \| None` | `str(TypeAdapter(HttpUrl).validate_python(raw))`. Returns `None` if the URL can't be parsed |
| `cap_host` | `(raw_url: str) -> str \| None` | The parser's host with brackets removed, lower-cased (already, per §1) and the trailing dot stripped. Returns `None` if unparsable |
| `plan_seeding` | `(stored: Sequence[StoredService], seeds: Sequence[SeedDefinition]) -> SeedPlan` | `StoredService` is `(id, url, origin, name, expected_status, check_interval, is_active)`. For each seed, find stored rows with an equal `normalized_url`. None → create. One or more → adopt the lowest id (set fields, origin `seeded`, active) and warn about the others. Rows with origin `seeded` whose normalized URL is in no seed → demote to `ordinary`. Rows whose URL is unparsable → ignored, with a warning. Returns `creates`, `updates` (id plus the new fields), `demotions` and `warnings`. Deterministic |
| `global_cap_reached` | `(visitor_count: int, cap: int) -> bool` | `visitor_count >= cap` |
| `per_host_cap_reached` | `(visitor_urls: Iterable[str], host: str, cap: int) -> bool` | Count of `cap_host(u) == host`, skipping `None` hosts, `>= cap` |

## 7. Algorithms (pseudocode)

### 7.1 Startup (RF-3 to RF-8)
```
lifespan:
  if settings.demo_mode:                                 # DEMO_MODE already strictly parsed at import (RF-1)
      config = load_demo_config()                        # raises DemoConfigError listing every error (RF-3)
      validate_seed_urls(config)                         # concurrent DNS; raises naming position+name (RF-4)
      with SessionLocal() as db, db.begin():             # one transaction (RF-5 note, RF-7)
          first_ids = apply_seed_plan(db, config)        # creates/adopts/demotes; logs warnings
      demo_settings.demo_config = config
      start_scheduler(first_check_ids=first_ids)         # interval jobs as today + one-off first checks (RF-8)
  else:
      start_scheduler()                                  # unchanged (RF-18, RNF-1)
# any exception above propagates -> lifespan startup fails -> the server refuses to start
```
`first_ids` holds every seeded id, created, adopted or already seeded, because RF-8 says each seeded service gets an immediate first check when the scheduler starts.

### 7.2 Create (RF-12 to RF-16)
```
create(payload, db):                                     # payload already passed request validation (RF-19 n/a)
  demo = demo_config
  interval = payload.check_interval
  if demo:
      if "check_interval" not in payload.model_fields_set: interval = demo.min_interval      # RF-12
      elif interval < demo.min_interval: raise DemoIntervalTooLow(interval)                  # RF-13
      if global_cap_reached(count_visitors(db), demo.global_cap): raise DemoCapReached(global)
      # (003b inserts its per-client cap check here)
      host = cap_host(str(payload.url))
      if per_host_cap_reached(visitor_urls(db), host, demo.per_host_cap): raise DemoCapReached(host)
  ensure_registration_allowed(payload.url.host, payload.submitted_url)                       # spec 002 DNS (422)
  url = str(payload.url)
  if duplicate_exists(db, url, exclude_id=None): raise DuplicateUrl                          # layer 1 (RF-14)
  try:
      with cap_guard(db, active=bool(demo)):             # PG: pg_advisory_xact_lock(DEMO_CAP_KEY); else threading.Lock
          if demo: re-run global and per-host checks (same order)                            # RF-15 atomic re-check
          insert Service(..., check_interval=interval, origin="visitor" if demo else "ordinary")
          db.commit()                                     # releases the advisory lock
  except IntegrityError:
      db.rollback()
      if duplicate_exists(db, url, exclude_id=None): raise DuplicateUrl                      # layer 2 (race)
      raise                                              # any other DB error is not a duplicate
  schedule_service(new)                                   # only after a successful commit
```
`duplicate_exists(db, url, exclude_id)` first does an exact `url == url` query, which uses the unique index. If that finds nothing, it scans `(id, url)` of all services and compares `normalized_url`. Unparsable rows never match.

### 7.3 Update (RF-10 to RF-16, RF-19)
```
update(service_id, payload, db):                         # RF-19 nulls already rejected by request validation
  service = get or raise ServiceNotFound                 # 404
  if demo:
      if service.origin == "seeded":   raise ServiceProtected(seeded)      # 403, even for an empty body
      if service.origin == "ordinary": raise ServiceProtected(ordinary)    # 403
      if "check_interval" in fields_set and payload.check_interval < demo.min_interval:
          raise DemoIntervalTooLow(payload.check_interval)                 # RF-13, even if unchanged
  new_url = str(payload.url) if "url" in fields_set else service.url
  host_changes = demo and cap_host(new_url) != cap_host(service.url)
  if host_changes and per_host_cap_reached(visitor_urls(db, exclude=service.id), cap_host(new_url), cap):
      raise DemoCapReached(host)
  if "url" in fields_set: ensure_registration_allowed(...)                 # spec 002 DNS
  if "url" in fields_set and duplicate_exists(db, new_url, exclude_id=service.id): raise DuplicateUrl
  try:
      with cap_guard(db, active=host_changes):
          if host_changes: re-check per-host
          apply the updates (setattr); db.commit()
  except IntegrityError: rollback; layer-2 duplicate check as in create; else raise
  except DemoCapReached: rollback; raise                 # nothing persisted, schedule untouched
  reschedule or unschedule as today (only after a successful commit)
```

### 7.4 Delete (RF-10, RF-11, RF-16)
```
delete(service_id, db):
  service = get or raise ServiceNotFound                 # 404 (a non-numeric id is a 422 from the path type)
  if demo and service.origin in ("seeded","ordinary"): raise ServiceProtected(...)   # 403
  db.delete(service); db.commit(); unschedule_service(service.id)   # schedule changes only after a successful commit (RF-16)
```
The unschedule moves to after the commit, so a failed delete leaves the schedule in place. That's a small ordering change with no API-visible effect.

### 7.5 Seed plan application (RF-5, RF-6)
```
apply_seed_plan(db, config):
  stored = [StoredService(...) for row in db.query(Service)]       # all rows, at most a few hundred in demo
  plan = plan_seeding(stored, config.seeds)
  for w in plan.warnings: log.warning(w)                           # extra matches, unparsable URLs
  for c in plan.creates: db.add(Service(**c, origin="seeded", is_active=True))
  for u in plan.updates: set fields on row u.id; origin="seeded"; is_active=True   # checks untouched (RF-9)
  for d in plan.demotions: row d.origin = "ordinary"
  db.flush()                                                       # ids for new rows; no commit (caller's transaction)
  return [ids of every seeded row]
```

## 8. Rendering / UI behavior
- No new endpoints and no response-shape changes. `ServiceResponse` stays the same, and the OpenAPI document is identical (verified for RF-19 in §1).
- With demo mode on, the API returns the 403, 409 and 422 responses in §5, in the RF-16 order.
- Logs: one INFO line summarizing seeding (created, adopted, demoted), plus WARNING lines for extra matches and unparsable stored URLs. Logs never contain credentials or resolved addresses (RF-4).

## 9. Technical decisions

| ID | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | Parse `DEMO_MODE` strictly in `Settings` at import, and keep the other demo settings in a separate class that is built only when demo mode is on | One settings class with every demo field | pydantic-settings raises at construction for a malformed field (verified in pass 2), so a single class would break RF-2. The separate class is never built while demo mode is off |
| D2 | Read demo settings as raw strings and parse them with pure functions | Typed pydantic fields | Typed `int` and `bool` accept `+20`, `20.0` (for some inputs), `t` and `y`. The spec requires decimal digits only and an exact boolean list |
| D3 | `origin` as `VARCHAR(8)` plus a CHECK constraint | A native PostgreSQL enum | Simpler migration and downgrade, it works on SQLite tests, and the schema check compares it cleanly |
| D4 | Serialize the cap step with a PostgreSQL transaction-level advisory lock (`pg_advisory_xact_lock`), with a process `threading.Lock` on other engines | `SELECT … FOR UPDATE` on a counter row / `SERIALIZABLE` isolation | A counter row is a second schema change, which RNF-2 doesn't allow. `SERIALIZABLE` needs retry loops. The advisory lock holds across processes, so the caps hold before 003d exists (RF-15, T2), and it is released automatically at commit or rollback |
| D5 | Layer-2 duplicate detection: on `IntegrityError`, roll back and re-query for a normalized duplicate. 409 if one exists, otherwise re-raise | Matching the constraint name | The constraint is named `uq_services_url` in the migrated database but `services_url_key` in the models' `create_all` used by the tests (measured in spec 001), so matching the name would be brittle. The re-query satisfies "any other database error is not reported as a duplicate" |
| D6 | Layer-1 duplicate detection: an exact indexed query first, then a full parse scan only when that finds nothing | A stored normalized-URL column | A new column is a schema change outside RNF-2. The scan is cheap at portfolio scale, and the exact query keeps the common case fast |
| D7 | Seeded first checks are one-off date jobs with `misfire_grace_time=None` | Changing the scheduler's global misfire grace | A global change would alter every check job, which breaks RNF-1. One-off jobs give the exemption to seeded first checks only (T1) |
| D8 | Move the write flows to `app/services/service_writes.py` | Growing the route handlers | AGENTS.md layering ("routes stay thin"). This also makes the RF-16 order and "nothing mutated before every check passes" testable without HTTP |
| D9 | Reuse spec 002's exception-plus-handler pattern for RF-13 | Raising FastAPI's `RequestValidationError` from the handler | Same shape as spec 002's 422, one consistent mechanism, and `input` is easy to control |
| D10 | Store the demo configuration once at startup in a module-level variable | Re-reading the environment per request | Settings are read once (spec). The variable makes tests simple through a fixture |
| D11 | Seed validation resolves hostnames concurrently in a thread pool sized to the number of seeds (at most 20) | Sequential resolution | RF-4 requires about one resolution timeout in total |

## 10. Test strategy

Logic is written test first (constitution principle 4): the pure functions in `tests/test_demo_rules.py` come before their implementation.

| Req | Automated (file) | Manual |
|---|---|---|
| RF-1 | `test_demo_startup.py`: every accepted spelling; invalid, empty and unset values; the import-time failure names `DEMO_MODE` | |
| RF-2 | `test_demo_startup.py`: demo mode off with every demo variable malformed still starts | |
| RF-3 | `test_demo_rules.py` (parsers), `test_demo_startup.py`: the error lists every bad variable, both variables for per-host > global, whole-list seed errors (invalid JSON, non-array, empty, more than 20), and integer spellings (`+20`, `20.0`, ` 20`, `1_000`) | |
| RF-4 | `test_demo_rules.py`: unknown key, wrong type (`true`, `"200"`, `1.0`), user information, a duplicate normalized URL, a URL the API rejects. `test_demo_startup.py`: an unsafe seed through the fake resolver, concurrent resolution timing, and error text with no credentials or addresses | |
| RF-5, RF-6, RF-9 | `test_demo_rules.py` (`plan_seeding`: create, adopt visitor or ordinary, reactivate, several matches choosing the lowest id, unparsable URL, demotion). `test_demo_startup.py`: applied at startup, checks kept, no duplicate on restart | |
| RF-7 | `test_demo_startup.py`: a seeding failure (forced DB error) stops startup, and no seeded rows are committed | |
| RF-8 | `test_demo_startup.py` with a real `BackgroundScheduler` (RNF-4): first-check jobs exist with `misfire_grace_time=None` and run immediately. With 20 seeds, 10 workers and slow targets, none is skipped. Interval jobs keep the default grace | |
| RF-10, RF-11 | `test_demo_writes.py`: 403 on update (empty and non-empty body), deactivate and delete, for seeded and ordinary services; nothing changed, schedule untouched | Running stack: PATCH a seeded service gives 403 |
| RF-12, RF-13 | `test_demo_writes.py`: create without an interval gets 300. 422 on create and update (present, equal to current, below the API range gives a validation error first); absent on update means no check | |
| RF-14 | `test_service_writes.py` (demo off) and `test_demo_writes.py` (demo on): exact and legacy (`http://Example.com`) duplicates on create and update give 409; the own URL is OK; a forced non-URL `IntegrityError` is not a 409. `postgres/test_demo_concurrency.py`: concurrent duplicates never give 500 | |
| RF-15 | `test_demo_rules.py` (cap decisions, cap host: case, trailing dot, port, punycode, IPv6). `test_demo_writes.py`: at and above each cap, a lowered cap. `postgres/test_demo_concurrency.py`: concurrent creates and host-changing updates never exceed a cap | |
| RF-16 | `test_demo_writes.py`: one test per adjacent pair in the create, update and delete orders. Every rejection leaves the row and the schedule unchanged | |
| RF-17 | | Read the docs against the code. `.env.example` keys equal what the settings read |
| RF-18 | `test_service_writes.py`: demo mode off, no caps and no protection | |
| RF-19 | `test_service_writes.py`: each field null, one entry per field, demo mode on and off. OpenAPI snapshot identical | |
| RNF-1 | All existing tests unchanged and green. OpenAPI JSON snapshot identical to `master` (spec 000 harness) | |
| RNF-2 | `postgres` suite: `test_full_history_matches_models` (the schema check) and a migration test on a seeded database (existing rows become `ordinary`) | |
| RNF-3 | `postgres/test_demo_concurrency.py` (threads, real PostgreSQL) | |
| RNF-5 | ruff, format, mypy, pytest (coverage at least 88), schema check, `pytest -m postgres` | |

## 11. Risks

| Risk | Mitigation |
|---|---|
| The coverage floor (88) drops because of new branches | Each new module comes with its unit tests in the same task. Current coverage is about 94.9% |
| A full-scan duplicate check on a large database (demo mode off) | The exact indexed query covers the common case. The scan is only for legacy forms. Documented as acceptable at portfolio scale (D6) |
| The advisory lock key collides with another application | The key is a constant derived from a fixed string (`service-monitoring-api:demo-caps`), and the lock is held only within one transaction |
| The startup DNS validation slows boot | Concurrent, about one resolution timeout (D11) |

## 12. Approvals needed
1. **Schema change:** the new `services.origin` column with a CHECK constraint, through one Alembic revision. The spec's RNF-2 requires plan approval.

No new dependency, no Docker or compose change, no file deletion.

## 13. RF coverage matrix

| Req | Plan sections |
|---|---|
| RF-1 | §3 `config.py`, §4.1, §6 `parse_demo_flag`, D1 |
| RF-2 | §3 `demo_settings.py`, §4.1, D1 |
| RF-3 | §4.1, §6, §7.1, D2 |
| RF-4 | §3 `demo_seeding.py`, §6 `parse_seed_list`, §7.1, D11 |
| RF-5 | §6 `plan_seeding`, §7.5 |
| RF-6 | §6, §7.5 |
| RF-7 | §7.1 |
| RF-8 | §3 `scheduler.py`, §7.1, D7 |
| RF-9 | §7.5 (checks untouched) |
| RF-10, RF-11 | §5, §7.3, §7.4 |
| RF-12, RF-13 | §5, §7.2, §7.3, D9 |
| RF-14 | §5, §7.2, §7.3, D5, D6 |
| RF-15 | §5, §6, §7.2, §7.3, D4 |
| RF-16 | §7.2 to §7.4, D8 |
| RF-17 | §3 docs rows |
| RF-18 | §7.1 else-branch, §7.2 to §7.4 (`demo` is `None`) |
| RF-19 | §3 `schemas/services.py`, §1, §5 |
| RNF-1 | §1, D7, §10 |
| RNF-2 | §3 model and migration, §4.2, D3 |
| RNF-3 | §10 |
| RNF-4 | §10 RF-8 |
| RNF-5 | §10 |
| RNF-6 | §10 |
