# Memory - Service Monitoring API

## Current state
- 2026-11-12: specs 000 and 001 merged to `master` (PR #1, `503610b`); push CI run 43315795094 green with all 5 checks. Both specs fully validated.
- v1.0 works: service CRUD (`/api/v1/services`), paginated metrics with time filter (`/api/v1/metrics/services/{id}`), aggregated status (`/api/v1/status`), `/health` liveness.
- Architecture: FastAPI routes -> services layer -> SQLAlchemy 2 sync models on PostgreSQL. APScheduler BackgroundScheduler runs one interval job per active service, started/stopped in the FastAPI lifespan. A service is DOWN after `FAIL_THRESHOLD` consecutive failed checks.
- Tests: 71 default tests (in-memory SQLite, scheduler mocked, coverage 88.41%, floor 88) plus 12 `postgres`-marked tests (migrations + endpoints on PostgreSQL, run with `TEST_POSTGRES_URL`).
- Known gaps: transitive dependencies not locked, the default test suite does not run on Postgres (only migrations + endpoint checks do; full parity is roadmap 004), no authentication, no metrics retention, obsolete `version:` key in docker-compose.yml.
- Known gap (constitution P3): default `check_interval` is hardcoded `60` in schema and model (the unused `SCHEDULER_INTERVAL_SECONDS` was removed in 000). Roadmap candidate: make the default interval a setting.
- Known gap (constitution P7): hardcoded Postgres credentials in docker-compose.yml. Not an approved exception; future spec.
- Spec 000-foundations: approved 2026-11-12 after two `/sdd-clarify` passes. Plan approved 2026-11-12.
- 000 baseline (Python 3.11.18, master): 50 tests, 281/319 statements = 88.09% -> coverage floor 88 (`fail_under=88`, `precision=2`). Margin is thin: one new uncovered statement fails the gate.
- Docker is reachable from WSL (Docker Desktop integration enabled 2026-11-12; server 29.2.1, Compose v2.40.3), so compose checks (RNF-1d, RNF-5) can run here.

## Decisions and why
- Per-service scheduler jobs instead of one global loop: each service has its own `check_interval`.
- Failure threshold before DOWN: avoids flapping on a single transient error.
- Sync SQLAlchemy sessions: the scheduler runs in threads, keeping one session model is simpler.
- SQLite for tests: fast, no external dependency (trade-off: no Postgres parity).
- 000 owner decisions: strict typing covers app + tests; migration history excluded from lint/format/type (approved exception to P8, never edited). Coverage floor = integer part of measured baseline line coverage of app code (honors "no regression"). Remove unused `SCHEDULER_INTERVAL_SECONDS`. Branch protection stays manual. pytest/pytest-cov move to a dev dependency list; Dockerfile unchanged.
- 000 plan decisions: ruff 0.17.2 (lint+format, line length 100 keeps query chains intact) and mypy 2.4.1 strict + pydantic plugin, both approved as new dev deps; `requirements-dev.txt` includes `requirements.txt`; typed `DeclarativeBase` + `Mapped[...]` models (prototype: identical DDL); `response_model=None` on `root()`/`health()` (without it OpenAPI changes); four independent CI jobs `CI / lint|format|typecheck|test`; equivalence check run ad hoc, not committed.
- 000 "no behavior change" is proven by: identical sorted-JSON OpenAPI snapshot, identical Postgres-dialect DDL from models, 50 tests unchanged in what they assert, manual compose run. Scheduler/session/check/timestamp/status code gets only mechanical + annotation-only changes.

- 000 implementation (2026-11-12, branch `feature/000-foundations`, draft PR #1): T1-T7, T9 done (CI green on PR #1: 4 checks, run 32s). T8 waits for the Mermaid render check on GitHub. T10: local evidence in `specs/000-foundations/validation.md`; throwaway broken-lint PR and cancellation check still pending; sign-off blocked until 001 lands.
- 000 OQ-1 (pre-existing on master): migrations never set `metrics.checked_at` server default / NOT NULL, so on Postgres every metric has `checked_at = NULL` -> metrics endpoint 500, uptime always 0.0. Owner decision: fix in spec 001 before 000 sign-off.
- Spec 001-migration-drift approved 2026-11-12 (branch `feature/001-migration-drift`, stacked on `feature/000-foundations`). Measured drift (migrated PG15 vs models): `metrics.checked_at` nullable + no default, `metrics.status` nullable + VARCHAR (model String(4)), `services.expected_status/check_interval/is_active` nullable. Alembic `compare_metadata` with `compare_type=True` did NOT flag the VARCHAR length difference, so the RF-7 check needs more than plain autogenerate compare.
- 001 owner decision (OQ-1 option a): the new migration DELETES legacy metrics (NULL `checked_at`) and reports the count. This is an approved one-time exception to constitution P1, limited to those rows. NULL service config columns get the model defaults; NULL or over-long `metrics.status` aborts the migration (never invent or truncate health data).
- 001 plan approved 2026-11-12: one hand-written migration (precheck status -> delete legacy + log count via alembic logger -> backfill service defaults -> defaults/NOT NULL/VARCHAR(4)); `python -m tools.schema_check` builds migrated DB + `create_all` DB on `TEST_POSTGRES_URL` and diffs inspector snapshots (types compiled for PG dialect, constraints compared by columns not names); `postgres`-marked tests deselected by default; new CI job `migrations` with PG15 service. Measured: migration ~2.4s on 1M rows; seeding 1M rows ~47s (RNF-3 is manual). PR #2 -> feature/000-foundations, then PR #1 -> master.
- 001 implemented 2026-11-12, all T1-T9 done, merged into `feature/000-foundations` via PR #2 (`aad8751`): migration `a62134b6f191`, `tools/schema_check.py`, 12 `postgres` tests. Verified: schema check 0 diffs, RNF-3 2.62s on 1M rows, compose smoke test shows timestamps + uptime 100.0, OpenAPI unchanged.
- Roadmap renumbered 2026-11-12: 001 migration-drift, 002 metrics-retention, 003 alerting, 004 postgres-test-parity, 005 api-auth.

- 2026-11-12: validation verdicts: 001 SPEC FULFILLED; 000 SPEC FULFILLED EXCEPT RF-6 (post-merge). Old template `docs/workflow-guide.md` (deleted in harness commit 19b941c) replaced by a guide for the current SDD workflow; sdd skill now documents validation.md, measuring before planning, per-task commits and mid-task bug handling.

## Lessons / mistakes to avoid
- Run `ruff format --check` before every push, including throwaway commits: an over-long comment broke the isolation of a CI validation run.
- Tests build tables with `create_all` from the models, so migration-vs-model drift is invisible to pytest; only a real `alembic upgrade head` on Postgres (compose) reveals it. Always smoke-test the write path end to end, not only `/health`.
- Formatter/linter edits touch every file: a spec that restricts sensitive areas to "annotation-only" must also allow mechanical (format/import) changes, or it is impossible to satisfy.
- FastAPI uses route return annotations as response models: typing routes without `response_model` can silently change OpenAPI.

## Next steps
- Owner: mark `lint`, `format`, `typecheck`, `test`, `migrations` as required checks in branch protection for `master` (manual).
- Next spec candidates (roadmap): 002-metrics-retention; also make the default `check_interval` a setting (P3 gap) and move compose credentials out of the file (P7 gap). Start with `/sdd-spec`.
