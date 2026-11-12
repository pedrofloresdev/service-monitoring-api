# Service Monitoring API

REST API that registers HTTP services, checks them on a per-service schedule, stores every check as a metric, and reports aggregated health (UP/DOWN, average response time, 24h uptime). Portfolio project for backend developers; v1.0 covers service CRUD, paginated metrics, and an aggregated status endpoint.

## Stack
- Python 3.11, FastAPI, Pydantic v2 + pydantic-settings
- PostgreSQL 15, SQLAlchemy 2.0 (sync sessions), Alembic migrations
- APScheduler (BackgroundScheduler, one interval job per active service), httpx for checks
- pip + `requirements.txt` (runtime) and `requirements-dev.txt` (ruff, mypy, pytest); Docker + docker compose
- Tool config in `pyproject.toml` (ruff, mypy strict, coverage floor); CI in `.github/workflows/ci.yml`

## Structure
- `app/api/v1/` - route handlers (services, metrics, status)
- `app/core/config.py` - settings from `.env`
- `app/db/` - engine/session, declarative base, ORM models
- `app/schemas/` - request/response schemas
- `app/services/` - business logic (monitor checks, status aggregation)
- `app/workers/scheduler.py` - scheduler lifecycle and per-service jobs
- `migrations/versions/` - Alembic revisions
- `tests/` - pytest, one file per area; `conftest.py` provides in-memory SQLite and mocks the scheduler
- `docs/` - constitution, product SPEC, architecture, SDD workflow guide
- `specs/NNN-name/` - spec.md, plan.md, tasks.md, validation.md per feature

## Commands
- Setup: `pip install -r requirements-dev.txt` (Python 3.11)
- Run: `alembic upgrade head && uvicorn app.main:app --reload` (or `docker compose up --build`)
- Build: `docker compose build`
- Test: `pytest` (fails below the coverage floor set in `pyproject.toml`)
- Lint: `ruff check .`
- Format: `ruff format .` (check only: `ruff format --check .`)
- Type check: `mypy` (strict, over `app/`, `tests/` and `tools/`)
- PostgreSQL checks (need `docker compose up -d db` and `TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres`): `python -m tools.schema_check` (migration history vs models) and `pytest -m postgres --no-cov`

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
- `ruff check .`, `ruff format --check .`, `mypy` and `pytest` all green (coverage at or above the floor, no regression). With PostgreSQL available, also `python -m tools.schema_check` and `pytest -m postgres --no-cov`. CI runs all five checks (`lint`, `format`, `typecheck`, `test`, `migrations`) on every PR.
- Manual: `docker compose up --build`, then `GET /health` and `GET /api/v1/status` respond; state what was verified.

## Memory
Read MEMORY.md at the start of every session. Update it at the end of each SDD phase.

## Workflow (SDD)
Constitution -> Spec -> Clarify -> Plan -> Tasks -> Implement ONE task -> Validate. Never advance a phase without explicit user approval. Commands: `/sdd-spec`, `/sdd-clarify`, `/sdd-plan`, `/sdd-tasks`, `/sdd-implement`, `/sdd-validate`, `/sdd-change`, `/sdd-status`. Walkthrough with worked examples (specs 000 and 001): `docs/workflow-guide.md`.
