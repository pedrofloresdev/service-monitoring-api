# Memory - Service Monitoring API

## Current state
- v1.0 works: service CRUD (`/api/v1/services`), paginated metrics with time filter (`/api/v1/metrics/services/{id}`), aggregated status (`/api/v1/status`), `/health` liveness.
- Architecture: FastAPI routes -> services layer -> SQLAlchemy 2 sync models on PostgreSQL. APScheduler BackgroundScheduler runs one interval job per active service, started/stopped in the FastAPI lifespan. A service is DOWN after `FAIL_THRESHOLD` consecutive failed checks.
- Tests: 50 pytest tests, ~88% coverage, in-memory SQLite, scheduler mocked.
- Known gaps: no linter/formatter, no type checker, no CI, no pyproject.toml, dependencies not locked, tests never run against Postgres, `.env.example` lists unused vars (SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES) and misses FAIL_THRESHOLD / CHECK_TIMEOUT_SECONDS, no authentication, no metrics retention, obsolete `version:` key in docker-compose.yml.

## Decisions and why
- Per-service scheduler jobs instead of one global loop: each service has its own `check_interval`.
- Failure threshold before DOWN: avoids flapping on a single transient error.
- Sync SQLAlchemy sessions: the scheduler runs in threads, keeping one session model is simpler.
- SQLite for tests: fast, no external dependency (trade-off: no Postgres parity).

## Lessons / mistakes to avoid
- (empty)

## Next steps
- Run `/sdd-spec 000-foundations`, then `/sdd-clarify`, `/sdd-plan`, `/sdd-tasks`.
