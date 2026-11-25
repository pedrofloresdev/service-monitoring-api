# Validation - 003a Demo mode

**Verdict (2026-11-24): SPEC FULFILLED.** Re-validation (reviewer on Sonnet, HEAD `9b8d854`): all 5 earlier gaps closed, OpenAPI snapshot equal to `master`, nothing regressed (526 passed, 97.20%, 20 postgres tests, schema check clean, `gh pr checks 8` green). Non-blocking notes: a stale-line refresh in this file (done) and the wording of the raised-minimum bullet in architecture §7 (an update without `check_interval` is not checked).
- First `/sdd-validate` (reviewer on Sonnet, 2026-11-24): NOT FULFILLED on test and doc gaps only, with no failing test or behaviour bug. The gaps:
  1. demo-on duplicates on update and legacy stored forms;
  2. the lowered per-host cap, the per-host boundary, and IPv6/punycode cap hosts through the API;
  3. "schedule unchanged" on create rejections and on the cap/hostname update rejections;
  4. no DNS in the atomic step, and the cap filled during the lookup;
  5. architecture §7 missing punycode, aliases, the raised minimum and trailing-dot variants.
- Fixes (this commit):
  - 20 new tests in `tests/test_demo_writes.py` (`TestDemoDuplicates`, `TestPerHostCap`, `TestRejectionsChangeNothing`, `TestAtomicCapStep`), mutation-checked: removing the cap re-check fails the 2 race tests, moving the DNS lookup inside the cap step fails the order test;
  - `tests/test_openapi_snapshot.py`, which compares against a snapshot generated from `master` (the optional finding on the plan's promised snapshot);
  - doc sentences in architecture §7.
- Gates after the fixes: 526 passed, coverage 97.20%, 20 postgres tests passed, ruff, format and mypy clean. CI: PR #8 run 45541629499 on `286d08a`, all 5 checks pass (lint, format, typecheck, test, migrations). All local evidence, including PostgreSQL and the compose run, collected 2026-11-24 (Docker 29.2.1 / Compose v2.40.3).

Branch `feature/003-demo-mode`, local evidence collected 2026-11-24 on WSL2, Python 3.11. `/sdd-validate` re-runs everything rather than trusting this file.

Commits: T1 `e9dee13`, T2 `3b93772`, T3 `29010fa`, T4 `080ecd8`, T5 `52b18f2`, T6 `4d07b60`, T7 `1b4103a`, T8 `a280ad0`, T9 `53b3edb` + `c9f938b`, T10 `bc95133` + this commit.

## Completion criteria

| Criterion | Result | Evidence |
|---|---|---|
| A fresh start with a seed list creates exactly the seeded services and checks each at once; a restart keeps them once each, history intact | PASS | `tests/test_demo_startup.py`: `test_seeds_are_created`, `test_restart_creates_no_duplicate_and_keeps_checks` (same id, metric kept), `test_adopts_corrects_reactivates_and_demotes`, `test_failure_refuses_startup_and_commits_nothing`. Real `BackgroundScheduler` (RNF-4): `test_twenty_slow_first_checks_with_ten_workers_none_skipped` (20 checks of 1.2 s, 10 workers, all run). With `misfire_grace_time=1` instead of `None`, the RF-8 tests fail, so they really test RF-8. Interval jobs keep grace 1 s. Compose (seeds with `check_interval` 3600): log `Demo seeding: 2 created, 0 adopted, 0 demoted`, then both seeds checked UP within 0.2 s of startup. After `docker restart`: `0 created, 2 adopted, 0 demoted`, same ids 1 and 2, checked again at once, `GET /api/v1/metrics/services/1` shows `total: 2` (history kept) |
| Every 403, 409 and 422 case and the RF-16 order, in automated tests and a manual run | PASS | `tests/test_demo_writes.py` (36): 403 seeded/ordinary on update (empty and non-empty body), deactivate and delete, nothing changed and schedule untouched; RF-12 default 300; RF-13 on create/update incl. equal-to-current and below-API-range ordering; global and per-host caps, cap-host change on update, trailing-dot/port host equality; RF-16 order pairs. `tests/test_service_writes.py` (17): RF-19 null 422, duplicates, scheduling after commit. Compose (global cap 2, per-host cap 1): PATCH and DELETE seeded -> 403 with the RF-10 body; POST without interval -> 201 with `check_interval` 300; POST interval 60 -> 422 `demo_interval_too_low` single entry with `input` 60; second visitor on `example.net` -> 409 per-host message; third visitor -> 409 global message; PATCH visitor to a seeded URL -> 409 duplicate; PATCH `name: null` -> 422 `null_not_allowed`; DELETE visitor -> 204. Run on a throwaway `demo_check` database via a scratch compose override (not committed), dropped afterwards |
| No duplicate URL produces a 500, sequentially or concurrently, demo on and off | PASS | Sequential: `test_service_writes.py` and `test_demo_writes.py` (exact and legacy `http://Example.com` forms, own URL OK, forced non-URL `IntegrityError` not a 409). Concurrent: `tests/postgres/test_demo_concurrency.py` (7 tests: global cap, per-host cap, host-changing updates, duplicate creates and updates demo on/off) `pytest -m postgres --no-cov`: 20 passed. Mutation-checked: without the advisory lock the 3 cap tests fail; without the commit-time `DuplicateUrl` conversion the 4 duplicate tests fail (a 0.2 s delay after each check widens the race window; before `c9f938b` the tests passed even without the lock) |
| Demo mode off: existing tests pass unchanged, API schema identical | PASS | Full suite 505 passed at T10, 526 after the validation fixes; the only changed spec-002 test is `url: null` -> 422 (RF-19, allowed by RNF-1). OpenAPI JSON of this branch vs a `master` worktree: `diff` empty (14,980 bytes each) |
| Docs and roadmap per RF-17; quality gates green in CI | PASS | `docs/architecture.md` §7 "Demo mode" (settings, seed format, origins, startup, protection, caps and cap host, error order, RF-14/RF-19, edge cases, need for 003b-003d); §2 delete order and §8 roadmap number fixed. `docs/SPEC.md` scope line linking §7; roadmap 003a-003d + order already in `bc99d63`. `README.md` "Running a public demo", 409/422 note in the API section, migration note. `.env.example` (T3). Local: `ruff check .` clean, `ruff format --check .` 89 files, `mypy` no issues in 52 files, `pytest` 505 passed, coverage 96.80% (floor 88) |

## Requirements

| Req | Result | Evidence |
|---|---|---|
| RF-1, RF-2, RF-3 | PASS | `tests/test_demo_rules.py` (94 total), `tests/test_demo_settings.py` (14, incl. subprocess app imports with invalid `DEMO_MODE`, malformed settings ignored while off, every error reported at once, per-host > global names both) |
| RF-4 | PASS | `TestValidateSeedUrls`: non-public seed refuses with position and name and no address; unresolvable accepted; 4 lookups of 0.5 s finish in < 1.5 s (concurrent). Seed-list parsing errors in `test_demo_rules.py` |
| RF-5, RF-6, RF-7, RF-9 | PASS | `TestSeedAtStartup`, `TestLifespan` (seeding failure stops startup; scheduler gets the first-check ids). Metrics are never touched by `apply_seed_plan` |
| RF-8 | PASS | `TestFirstChecks` (real scheduler), mutation-checked as above |
| RF-10 to RF-16 | PASS | `test_demo_writes.py`, `test_service_writes.py`, `test_demo_concurrency.py`, compose run |
| RF-17 | PASS | Docs listed above |
| RF-18 | PASS | Demo-off suite unchanged; `demo_config` is `None` unless the lifespan sets it |
| RF-19 | PASS | `test_service_writes.py`, `test_services_url_safety.py::test_update_with_null_url_is_a_validation_error`; OpenAPI unchanged |
| RNF-1 | PASS | As "Demo mode off" above |
| RNF-2 | PASS | Migration `30fbc6a1211a`; `tests/postgres/test_demo_migration.py` passed; `python -m tools.schema_check` -> No schema differences between the migration history and the models |
| RNF-3 | PASS | `tests/postgres/test_demo_concurrency.py`, mutation-checked as above |
| RNF-4 | PASS | Real `BackgroundScheduler` in `test_demo_startup.py` |
| RNF-5 | PASS | Gates above; CI green on PR #8 |

## Pending

None. Merge PR #8 (owner).
