# Memory - Service Monitoring API

## Current state
- v1.0 works: service CRUD (`/api/v1/services`), paginated metrics with time filter (`/api/v1/metrics/services/{id}`), aggregated status (`/api/v1/status`), `/health` liveness.
- Architecture: FastAPI routes -> services layer -> SQLAlchemy 2 sync models on PostgreSQL. APScheduler BackgroundScheduler runs one interval job per active service, started/stopped in the FastAPI lifespan. A service is DOWN after `FAIL_THRESHOLD` consecutive failed checks.
- Tests: 50 pytest tests, ~88% coverage, in-memory SQLite, scheduler mocked.
- Known gaps: no linter/formatter, no type checker, no CI, no pyproject.toml, dependencies not locked, tests never run against Postgres, `.env.example` lists unused vars (SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES) and misses FAIL_THRESHOLD / CHECK_TIMEOUT_SECONDS, no authentication, no metrics retention, obsolete `version:` key in docker-compose.yml.
- Known gap (constitution P3): default `check_interval` is hardcoded `60` in schema and model; `SCHEDULER_INTERVAL_SECONDS` is declared but never read (to be removed in 000). Roadmap candidate: make the default interval a setting.
- Known gap (constitution P7): hardcoded Postgres credentials in docker-compose.yml. Not an approved exception; future spec.
- Spec 000-foundations: approved 2026-11-12 after two `/sdd-clarify` passes.

## Decisions and why
- Per-service scheduler jobs instead of one global loop: each service has its own `check_interval`.
- Failure threshold before DOWN: avoids flapping on a single transient error.
- Sync SQLAlchemy sessions: the scheduler runs in threads, keeping one session model is simpler.
- SQLite for tests: fast, no external dependency (trade-off: no Postgres parity).
- 000 owner decisions: strict typing covers app + tests; migration history excluded from lint/format/type (approved exception to P8, never edited). Coverage floor = integer part of measured baseline line coverage of app code (honors "no regression"). Remove unused `SCHEDULER_INTERVAL_SECONDS`. Branch protection stays manual. pytest/pytest-cov move to a dev dependency list; Dockerfile unchanged.
- 000 "no behavior change" is proven by: identical sorted-JSON OpenAPI snapshot, identical Postgres-dialect DDL from models, 50 tests unchanged in what they assert, manual compose run. Scheduler/session/check/timestamp/status code gets only mechanical + annotation-only changes.

## Lessons / mistakes to avoid
- Formatter/linter edits touch every file: a spec that restricts sensitive areas to "annotation-only" must also allow mechanical (format/import) changes, or it is impossible to satisfy.
- FastAPI uses route return annotations as response models: typing routes without `response_model` can silently change OpenAPI.

## Next steps
- Run `/sdd-plan 000-foundations` (first step: measure baseline coverage to fix the floor; plan must get approval for new dev dependencies).
