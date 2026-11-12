# Service Monitoring API

REST API that registers HTTP services, checks them on a per-service schedule, stores every check as a metric, and reports aggregated health (UP/DOWN, average response time, 24h uptime). Portfolio project for backend developers; v1.0 covers service CRUD, paginated metrics, and an aggregated status endpoint.

## Stack
- Python 3.11, FastAPI, Pydantic v2 + pydantic-settings
- PostgreSQL 15, SQLAlchemy 2.0 (sync sessions), Alembic migrations
- APScheduler (BackgroundScheduler, one interval job per active service), httpx for checks
- pip + `requirements.txt`; Docker + docker compose

## Structure
- `app/api/v1/` - route handlers (services, metrics, status)
- `app/core/config.py` - settings from `.env`
- `app/db/` - engine/session, declarative base, ORM models
- `app/schemas/` - request/response schemas
- `app/services/` - business logic (monitor checks, status aggregation)
- `app/workers/scheduler.py` - scheduler lifecycle and per-service jobs
- `migrations/versions/` - Alembic revisions
- `tests/` - pytest, one file per area; `conftest.py` provides in-memory SQLite and mocks the scheduler
- `docs/` - constitution and product SPEC
- `specs/NNN-name/` - spec.md, plan.md, tasks.md per feature

## Commands
- Run: `alembic upgrade head && uvicorn app.main:app --reload` (or `docker compose up --build`)
- Build: `docker compose build`
- Test: `pytest` (coverage report included via pytest.ini)
- Lint/format: not set up yet
- Type check: not set up yet

## Conventions
- Everything in English (code, comments, identifiers, commits, docs).
- Git: branches `feature/`, `bugfix/`, `hotfix/` + kebab-case name. Never commit directly to `master`. Conventional commits.
- Layering: routes stay thin -> `app/services/` holds logic -> `app/db/models/` for persistence. Schemas define every request/response.
- Tunables (thresholds, timeouts, intervals) go in `app/core/config.py`, never hardcoded.
- Schema changes only through Alembic revisions.

## Domain pitfalls
- Scheduler jobs run in background threads: each job opens and closes its own `SessionLocal()`; never reuse a request session.
- Tests run on SQLite: Postgres-only behavior (timezones, types) can pass tests and fail in prod.
- Keep the scheduler in sync with service CRUD (create/update/delete/toggle must schedule or unschedule).
- Avoid N+1 queries in status aggregation; `checked_at` is indexed for time-range queries.
- No secrets as settings defaults.

## Limits
- ✅ Always: follow the active spec, write tests first for logic, run tests/lint/type check before saying done, update MEMORY.md at the end of each phase.
- ⚠️ Ask first: new dependencies, schema/migration changes, deleting files, Docker/deploy config, anything touching production or personal data.
- 🚫 Never: implement what is not in the spec, commit secrets (`.env`), skip failing tests, advance an SDD phase without approval.

## Verification
- `pytest` green with no coverage regression; lint and type check clean once set up.
- Manual: `docker compose up --build`, then `GET /health` and `GET /api/v1/status` respond; state what was verified.

## Memory
Read MEMORY.md at the start of every session. Update it at the end of each SDD phase.

## Workflow (SDD)
Constitution -> Spec -> Clarify -> Plan -> Tasks -> Implement ONE task -> Validate. Never advance a phase without explicit user approval. Commands: `/sdd-spec`, `/sdd-clarify`, `/sdd-plan`, `/sdd-tasks`, `/sdd-implement`, `/sdd-validate`, `/sdd-change`, `/sdd-status`.
