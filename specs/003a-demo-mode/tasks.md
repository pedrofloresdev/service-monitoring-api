# 003a - Demo mode: seeding, protection and caps — tasks

Status: approved (2026-11-24)
Spec: `spec.md` (approved) · Plan: `plan.md` (approved, including the `services.origin` migration)

Rules for every task:
- Implement one task, then stop.
- Logic is written test first (red, then green).
- After each task: `ruff check .`, `ruff format --check .`, `mypy`, and `pytest` with coverage ≥ 88.
- When a task touches persistence, also run `python -m tools.schema_check` and `pytest -m postgres --no-cov` (with `docker compose up -d db` and `TEST_POSTGRES_URL`).
- Commit each task with `Refs: specs/003a-demo-mode Tn`.

## Tasks

- [x] **T1. Pure parsing and cap rules, test first.** Write `tests/test_demo_rules.py` first, then add to `app/services/demo_rules.py`: `parse_demo_flag`, `parse_strict_int`, `normalized_url`, `cap_host`, `global_cap_reached` and `per_host_cap_reached` (plan §6). RF-1, RF-3, RF-15
  - Done when:
    - The new tests failed before the functions existed and pass after.
    - They cover every accepted `DEMO_MODE` spelling plus invalid, empty and unset values.
    - They cover integer spellings (`20` OK; `+20`, `20.0`, ` 20`, `1_000` and out of range rejected).
    - They cover cap-host derivation: case, trailing dot, port, punycode, an IPv6 literal, an unparsable URL.
    - They cover each cap at and above its boundary.
    - All quality checks are green.

- [x] **T2. Seed-list parser and seed planner, test first.** Write the tests first, then add `parse_seed_list` and `plan_seeding` (plan §6) to `app/services/demo_rules.py`. RF-3, RF-4, RF-5, RF-6
  - Done when:
    - The tests failed first and pass after.
    - The parser tests cover whole-list errors (invalid JSON, a non-array, an empty list, more than 20 entries), unknown keys, wrong types (`true`, `"200"`, `1.0`), user information, a URL the API rejects, and a duplicate normalized URL.
    - They cover error text with a 1-based position and the name, without credentials, and the defaults taken from `ServiceCreate`.
    - The planner tests cover: create; adopt a visitor service and an ordinary service; reactivate; several matches choosing the lowest id with warnings; an unparsable stored URL ignored with a warning; demotion of a removed seed.
    - All quality checks are green.

- [ ] **T3. Settings wiring.** Make these changes:
  - `app/core/config.py`: `DEMO_MODE` parsed strictly at import.
  - New `app/core/demo_settings.py`: `DemoSettings` with raw string fields, `load_demo_config()` collecting every error, and the module-level `demo_config`.
  - `tests/conftest.py`: a `demo_config` fixture.
  - `.env.example`: the demo variables.

  RF-1, RF-2, RF-3
  - Done when:
    - An invalid `DEMO_MODE` fails at import with an error naming it.
    - With demo mode off and every other demo variable malformed, the app imports and starts.
    - `load_demo_config()` reports every bad variable at once, and names both variables when per-host > global.
    - The `.env.example` keys equal the variables the settings read.
    - All existing tests pass unchanged, and all quality checks are green.

- [ ] **T4. Service origin column and migration.** Add `ServiceOrigin` and the `origin` column (`VARCHAR(8)`, NOT NULL, server default `'ordinary'`, CHECK `ck_services_origin`) to the model. Create the Alembic revision with `alembic revision` and write its upgrade and downgrade by hand. RNF-2
  - Done when:
    - `python -m tools.schema_check` reports no differences.
    - A new `postgres` test shows that migrating a database with existing services sets them all to `ordinary`, and that the downgrade removes the column.
    - `pytest -m postgres --no-cov` and all quality checks are green.
    - Existing migrations are unchanged (`git diff` shows one added revision).

- [ ] **T5. `null` on required fields returns 422.** Add the `mode="before"` validator on `ServiceUpdate` for `name`, `url`, `expected_status`, `check_interval` and `is_active` (plan §1, §5). RF-19
  - Done when:
    - The tests written first (in `tests/test_service_writes.py`) show one `null_not_allowed` entry per null field, with message `<field> cannot be null`, for every field and for a combination, with demo mode on and off.
    - The OpenAPI JSON snapshot is byte-identical to the one from `master`.
    - All quality checks are green.

- [ ] **T6. Write flows module with demo mode off: duplicates and commit order.** Create `app/services/service_writes.py` with the create, update and delete flows (plan §7.2–§7.4, demo branches not yet active) and two-layer duplicate detection (D5, D6). Make the routes thin. Schedule and unschedule only after a successful commit. RF-14, RF-16, RF-18
  - Done when:
    - The tests written first (in `tests/test_service_writes.py`, demo mode off) show 409 for exact and legacy (`http://Example.com`) duplicates on create and update.
    - Keeping a service's own URL is accepted.
    - A forced non-URL `IntegrityError` is not turned into a 409.
    - A failed write leaves the row and the schedule unchanged.
    - All existing tests pass unchanged, the OpenAPI JSON is identical, and all quality checks are green.

- [ ] **T7. Demo write rules: origin, interval, protection, caps, order.** Make these changes in the write flows (plan §7.2–§7.4):
  - Add the demo branches: origin `visitor`, the RF-12 default interval, RF-13, 403 protection, the global and per-host caps.
  - Add the `cap_guard` atomic step: a PostgreSQL advisory lock, or a threading lock on other databases (D4).
  - Add the domain exceptions, and their handlers in `app/main.py`, with the exact bodies from plan §5.

  RF-10 to RF-16
  - Done when the tests written first in `tests/test_demo_writes.py` pass. They show:
    - every §5 body exactly;
    - 403 on update (empty and non-empty body), deactivate and delete, for seeded and ordinary services;
    - RF-12 with and without `check_interval`;
    - RF-13 present, absent, equal to current, and below the API range;
    - each cap at and above its boundary, and a lowered cap;
    - one test per adjacent pair in the RF-16 create, update and delete orders;
    - every rejection leaving the row and the schedule unchanged.

    All quality checks are green.

- [ ] **T8. Seeding at startup with immediate first checks.** Make these changes:
  - Add `validate_seed_urls`, which resolves hostnames concurrently, and `apply_seed_plan` in `app/services/demo_seeding.py`.
  - In the lifespan, while demo mode is on: load the config, validate, seed in one transaction, then start the scheduler with first-check ids.
  - Add `start_scheduler(first_check_ids)` and `schedule_first_check` (one-off jobs, `misfire_grace_time=None`).

  RF-4 to RF-9
  - Done when the tests in `tests/test_demo_startup.py` pass, using a real `BackgroundScheduler`. They show:
    - seeds created, adopted and demoted at startup, with existing checks kept and no duplicate after a restart;
    - an unsafe seed (fake resolver) refusing startup, with error text free of credentials and addresses;
    - resolution running concurrently (about one resolution timeout for several seeds);
    - a forced seeding failure refusing startup with nothing committed;
    - first-check jobs with `misfire_grace_time=None` running at once, none skipped with 20 seeds, 10 workers and slow targets;
    - interval jobs keeping their default grace.

    All quality checks are green.

- [ ] **T9. PostgreSQL concurrency tests.** Write `tests/postgres/test_demo_concurrency.py`, with threads against real PostgreSQL. RNF-3
  - Done when, with local PG, the tests show:
    - concurrent creates at each cap boundary never exceed the cap;
    - concurrent host-changing updates never exceed the per-host cap;
    - concurrent duplicate creates and updates always give 409, never 500.

    `pytest -m postgres --no-cov` passes, and the default suite and the quality checks are green.

- [ ] **T10. Docs and validation.** Make these changes:
  - `docs/architecture.md`: a new §7 "Demo mode".
  - `docs/SPEC.md`: a current-scope line linking to §7.
  - `README.md`: a "Running a public demo" section.
  - `specs/003a-demo-mode/validation.md`: the evidence for every completion criterion.

  RF-17, RNF-1, RNF-5
  - Done when:
    - The docs match the code (settings, error bodies, order, edge cases, the need for 003b to 003d).
    - The OpenAPI JSON is identical to `master`.
    - A manual run against the compose stack with demo mode on and a seed list shows the seeds checked at once, a 403 on a seeded service, a cap 409, the RF-12 default interval, and a duplicate 409 on update.
    - CI is green on a PR (pushed with the owner's confirmation).

## Coverage check

| Req | Tasks |
|---|---|
| RF-1 | T1, T3 |
| RF-2 | T3 |
| RF-3 | T1, T2, T3 |
| RF-4 | T2, T8 |
| RF-5, RF-6 | T2, T8 |
| RF-7 | T8 |
| RF-8 | T8 |
| RF-9 | T8 |
| RF-10, RF-11 | T7 |
| RF-12, RF-13 | T7 |
| RF-14 | T6, T9 |
| RF-15 | T1, T7, T9 |
| RF-16 | T6, T7 |
| RF-17 | T3 (`.env.example`), T10 |
| RF-18 | T6 |
| RF-19 | T5 |
| RNF-1 | T5, T6, T10 |
| RNF-2 | T4 |
| RNF-3 | T9 |
| RNF-4 | T8 |
| RNF-5 | every task |
| RNF-6 | T1 to T9 |
