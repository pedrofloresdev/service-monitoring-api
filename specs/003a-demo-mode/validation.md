# Validation - 003a Demo mode

**Verdict: PENDING.** Some evidence needs PostgreSQL/Docker, which wasn't reachable from WSL when this file was written (2026-11-24), and CI. `/sdd-validate` hasn't run yet.

Branch `feature/003-demo-mode`, local evidence collected 2026-11-24 on WSL2, Python 3.11. `/sdd-validate` re-runs everything rather than trusting this file.

Commits: T1 `e9dee13`, T2 `3b93772`, T3 `29010fa`, T4 `080ecd8`, T5 `52b18f2`, T6 `4d07b60`, T7 `1b4103a`, T8 `a280ad0`, T9 `53b3edb`, T10 (this commit).

## Completion criteria

| Criterion | Result | Evidence |
|---|---|---|
| A fresh start with a seed list creates exactly the seeded services and checks each at once; a restart keeps them once each, history intact | PASS (automated), PENDING (compose) | `tests/test_demo_startup.py`: `test_seeds_are_created`, `test_restart_creates_no_duplicate_and_keeps_checks` (same id, metric kept), `test_adopts_corrects_reactivates_and_demotes`, `test_failure_refuses_startup_and_commits_nothing`. Real `BackgroundScheduler` (RNF-4): `test_twenty_slow_first_checks_with_ten_workers_none_skipped` (20 checks of 1.2 s, 10 workers, all run). With `misfire_grace_time=1` instead of `None`, the RF-8 tests fail, so they really test RF-8. Interval jobs keep grace 1 s |
| Every 403, 409 and 422 case and the RF-16 order, in automated tests and a manual run | PASS (automated), PENDING (compose) | `tests/test_demo_writes.py` (36): 403 seeded/ordinary on update (empty and non-empty body), deactivate and delete, nothing changed and schedule untouched; RF-12 default 300; RF-13 on create/update incl. equal-to-current and below-API-range ordering; global and per-host caps, cap-host change on update, trailing-dot/port host equality; RF-16 order pairs. `tests/test_service_writes.py` (17): RF-19 null 422, duplicates, scheduling after commit |
| No duplicate URL produces a 500, sequentially or concurrently, demo on and off | PASS (sequential), PENDING (concurrent) | Sequential: `test_service_writes.py` and `test_demo_writes.py` (exact and legacy `http://Example.com` forms, own URL OK, forced non-URL `IntegrityError` not a 409). Concurrent: `tests/postgres/test_demo_concurrency.py` (7 tests: global cap, per-host cap, host-changing updates, duplicate creates and updates demo on/off) written; needs `pytest -m postgres --no-cov` |
| Demo mode off: existing tests pass unchanged, API schema identical | PASS | Full suite 505 passed; the only changed spec-002 test is `url: null` -> 422 (RF-19, allowed by RNF-1). OpenAPI JSON of this branch vs a `master` worktree: `diff` empty (14,980 bytes each) |
| Docs and roadmap per RF-17; quality gates green in CI | PASS (docs, local gates), PENDING (CI) | `docs/architecture.md` §7 "Demo mode" (settings, seed format, origins, startup, protection, caps and cap host, error order, RF-14/RF-19, edge cases, need for 003b-003d); §2 delete order and §8 roadmap number fixed. `docs/SPEC.md` scope line linking §7; roadmap 003a-003d + order already in `bc99d63`. `README.md` "Running a public demo", 409/422 note in the API section, migration note. `.env.example` (T3). Local: `ruff check .` clean, `ruff format --check .` 89 files, `mypy` no issues in 52 files, `pytest` 505 passed, coverage 96.80% (floor 88) |

## Requirements

| Req | Result | Evidence |
|---|---|---|
| RF-1, RF-2, RF-3 | PASS | `tests/test_demo_rules.py` (94 total), `tests/test_demo_settings.py` (14, incl. subprocess app imports with invalid `DEMO_MODE`, malformed settings ignored while off, every error reported at once, per-host > global names both) |
| RF-4 | PASS | `TestValidateSeedUrls`: non-public seed refuses with position and name and no address; unresolvable accepted; 4 lookups of 0.5 s finish in < 1.5 s (concurrent). Seed-list parsing errors in `test_demo_rules.py` |
| RF-5, RF-6, RF-7, RF-9 | PASS | `TestSeedAtStartup`, `TestLifespan` (seeding failure stops startup; scheduler gets the first-check ids). Metrics are never touched by `apply_seed_plan` |
| RF-8 | PASS | `TestFirstChecks` (real scheduler), mutation-checked as above |
| RF-10 to RF-16 | PASS (SQLite), PENDING (PostgreSQL concurrency, RNF-3) | `test_demo_writes.py`, `test_service_writes.py`, `test_demo_concurrency.py` pending |
| RF-17 | PASS | Docs listed above |
| RF-18 | PASS | Demo-off suite unchanged; `demo_config` is `None` unless the lifespan sets it |
| RF-19 | PASS | `test_service_writes.py`, `test_services_url_safety.py::test_update_with_null_url_is_a_validation_error`; OpenAPI unchanged |
| RNF-1 | PASS | As "Demo mode off" above |
| RNF-2 | PASS (code, offline SQL), PENDING (PostgreSQL) | Migration `30fbc6a1211a`; `tests/postgres/test_demo_migration.py` and `python -m tools.schema_check` need PostgreSQL |
| RNF-3 | PENDING | `tests/postgres/test_demo_concurrency.py` |
| RNF-4 | PASS | Real `BackgroundScheduler` in `test_demo_startup.py` |
| RNF-5 | PASS (local), PENDING (CI) | Gates above |

## Pending, to run with Docker up

1. `docker compose up -d db`, then with `TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres`: `python -m tools.schema_check` and `pytest -m postgres --no-cov`. Tick T4 and T9.
2. Manual compose run with `DEMO_MODE=true` and a seed list: seeds checked at once, 403 on a seeded service, a cap 409, the default interval 300, a duplicate 409 on update.
3. Push and open the PR (owner confirmation), CI green.
