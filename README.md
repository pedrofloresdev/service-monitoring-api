# Service Monitoring API

A production-ready backend system that monitors publicly reachable HTTP services, tracks uptime, and delivers real-time observability through automated health checks and performance metrics.

---

## Overview

**Service Monitoring API** simulates a real-world observability platform. Register any HTTP endpoint and the system will continuously probe it on its own schedule, record response times, detect failures intelligently, and expose aggregated health data through a clean REST API.

---

## Key Features

### Automated Health Checks

- Background scheduler (APScheduler) runs checks per service on its own configurable interval
- Each check records HTTP status code and response time
- `httpx` with configurable timeouts — no thread blocking

### Intelligent Status Detection

- Marks a service DOWN only after N consecutive failures (configurable `FAIL_THRESHOLD`)
- Avoids false positives from transient network blips
- Status reverts to UP as soon as a successful check is recorded

### Per-Service Scheduling

- Each service has its own `check_interval` (10s – 86400s)
- Scheduler jobs are created, updated, and removed dynamically as services are registered, updated, or deleted

### Metrics Collection & Aggregation

- Stores response time (`Float` precision) and HTTP status per check
- Time-range filtering (`?hours=24`) and cursor-style pagination on the metrics endpoint
- Uptime percentage calculated over the last 24 hours

### Full CRUD for Services

- Register, list, update (partial PATCH), and delete monitored services
- Deactivating a service (`is_active: false`) removes it from the scheduler without deleting its history
- Duplicate URL detection with 409 Conflict

### Production-ready Infrastructure

- Docker + Docker Compose with PostgreSQL health-check and proper `depends_on` condition
- Alembic migrations run automatically on container startup
- Docker `HEALTHCHECK` on the API container
- Structured logging across all layers

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI |
| Database | PostgreSQL 15 |
| ORM / migrations | SQLAlchemy 2 + Alembic |
| HTTP client | httpx |
| Scheduler | APScheduler 3 |
| Validation | Pydantic v2 + pydantic-settings |
| Containerization | Docker + Docker Compose |

---

## Architecture

```
app/
├── api/v1/          # Route handlers (services, metrics, status)
├── core/            # Settings via pydantic-settings
├── db/
│   ├── base.py      # Declarative base
│   ├── session.py   # Engine + session factory
│   └── models/      # SQLAlchemy ORM models
├── schemas/         # Pydantic request / response schemas
├── services/        # Business logic (monitor, status aggregation)
├── workers/         # APScheduler lifecycle management
└── main.py          # App factory + lifespan
```

For how a health check flows from the scheduler to a stored metric, and how UP/DOWN, average response time and uptime are derived, see [docs/architecture.md](docs/architecture.md).

---

## API Reference

All routes are prefixed with `/api/v1`.

### Services

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/services` | Register a new service |
| `GET` | `/services` | List all services (`?active_only=true`) |
| `GET` | `/services/{id}` | Get a single service |
| `PATCH` | `/services/{id}` | Update service config or toggle active state |
| `DELETE` | `/services/{id}` | Remove service and its metrics |

**POST /services body:**

```json
{
  "name": "My API",
  "url": "https://api.example.com/health",
  "expected_status": 200,
  "check_interval": 60
}
```

A create or update that would give two services the same URL returns `409` (`"A service with this URL already exists"`). Setting a field to `null` in `PATCH` returns `422`.

---

### Metrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/metrics/services/{id}` | Paginated metrics for a service |

**Query params:** `limit` (1–1000, default 100) · `offset` · `hours` (restrict to last N hours)

---

### Status

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/status` | Aggregated health for all active services |

**Response:**

```json
{
  "total_services": 3,
  "up": 2,
  "down": 1,
  "services": [
    {
      "id": 1,
      "name": "My API",
      "url": "https://api.example.com/health",
      "status": "UP",
      "avg_response_time_ms": 142.5,
      "uptime_last_24h": 99.31
    }
  ]
}
```

---

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/` | API info |

---

## Running Locally

### With Docker (recommended)

```bash
git clone https://github.com/flobell/service-monitoring-api.git
cd service-monitoring-api

cp .env.example .env       # review and adjust if needed

docker compose up --build
```

Migrations run automatically. API is available at `http://localhost:8000`.

Interactive docs: `http://localhost:8000/docs`

---

### Without Docker

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt               # runtime deps + dev tools (Python 3.11)

cp .env.example .env    # then set DATABASE_URL to your PostgreSQL
alembic upgrade head
uvicorn app.main:app --reload
```

---

## Quality Checks

Tool configuration lives in `pyproject.toml`. Install the dev tools with `pip install -r requirements-dev.txt`, then:

| Check | Command |
|---|---|
| Lint | `ruff check .` |
| Format | `ruff format .` (check only: `ruff format --check .`) |
| Type check | `mypy` (strict mode, `app/`, `tests/` and `tools/`) |
| Tests + coverage floor | `pytest` (fails if coverage of `app/` drops below the floor in `pyproject.toml`) |

### PostgreSQL checks (migrations)

The default test suite runs on in-memory SQLite. Two extra checks need a real PostgreSQL server, given as an admin URL where databases can be created and dropped:

```bash
cp .env.example .env    # if you have no .env yet (docker compose needs it)
docker compose up -d db
export TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres

python -m tools.schema_check     # migration history vs models: exit 0 = identical, 1 = differences
pytest -m postgres --no-cov      # migration behavior and endpoints on the migrated schema
```

`TEST_POSTGRES_URL` is a tooling variable, not an app setting. Without it, both commands fail with these instructions instead of passing silently.

### Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs five independent jobs on every pull request to `master` and on every push to `master`. Each job reports its own status: `lint`, `format`, `typecheck`, `test` and `migrations`. The `migrations` job runs the PostgreSQL checks against a disposable PostgreSQL 15 container. To block merges while a check fails, mark these five as required status checks in the repository's branch protection settings for `master`.

### How changes are made

Every change follows Spec-Driven Development: an approved spec in `specs/NNN-name/`, then a plan, small tasks, test-first implementation and a validation report with evidence. See [docs/workflow-guide.md](docs/workflow-guide.md).

### Upgrading an existing database

Migration `a62134b6f191` (spec 001) fixes a bug where checks were stored without a timestamp on PostgreSQL. It **deletes** those legacy metrics, because their real time is unknown and they could not be shown, and logs how many it deleted (`Deleted N legacy metric(s) with NULL checked_at`). Back up the database before running `alembic upgrade head`. If any metric has an empty or invalid status, the migration stops without changing anything.

Migration `30fbc6a1211a` (spec 003a) adds the `services.origin` column used by demo mode. Existing services become `ordinary`; no data is removed.

---

## Configuration

All settings are loaded from `.env` via `pydantic-settings`. `.env.example` lists exactly these, plus the demo-mode settings in [Running a public demo](#running-a-public-demo).

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(required)* | PostgreSQL connection string |
| `FAIL_THRESHOLD` | `3` | Consecutive failures before marking a service DOWN |
| `CHECK_TIMEOUT_SECONDS` | `10` | HTTP request timeout per check |
| `RESOLUTION_TIMEOUT_SECONDS` | `2` | Maximum time to resolve a service hostname (IPv4 and IPv6 together) on create/update and before every check; decimal, > 0 and <= 30 |

The default `check_interval` for a new service (60 seconds) is fixed in code, not a setting; each service can set its own interval (10–86400 seconds).

**Container-only variables** (not read by the application, so not in `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | Port uvicorn listens on inside the API container (used by the Dockerfile `CMD` and `HEALTHCHECK`) |

With `docker compose`, the API container's `DATABASE_URL` is set in `docker-compose.yml` and overrides the value from `.env`.

---

## Running a public demo

> **Not ready for public use yet.** Demo mode (spec 003a) gives you protected example services and capped visitor writes. A public deployment also needs rate limiting (spec 003b), a periodic reset of visitor data (003c) and a single-process lock (003d), which are not implemented yet.

Turn it on in `.env` and list the example services:

```bash
DEMO_MODE=true
DEMO_SEED_SERVICES=[{"name": "Example", "url": "https://example.com/", "check_interval": 60}]
```

| Variable | Default | Description |
|---|---|---|
| `DEMO_MODE` | off | `true`/`false` (also `1`/`0`, `yes`/`no`, `on`/`off`). Any other value stops startup |
| `DEMO_SEED_SERVICES` | required | JSON array of 1–20 services: `name`, `url`, optional `expected_status` and `check_interval` |
| `DEMO_GLOBAL_VISITOR_CAP` | `20` | Visitor services allowed at once (1–1000) |
| `DEMO_PER_HOST_VISITOR_CAP` | `3` | Visitor services allowed per host (1–1000, at most the global cap) |
| `DEMO_MIN_CHECK_INTERVAL_SECONDS` | `300` | Minimum check interval for visitor services, also their default (10–86400) |

The `DEMO_*` settings are read only while demo mode is on; any invalid value stops startup with a message naming it.

What changes while it is on:

- The seed services are created (or adopted, if their URL already exists) at startup and checked immediately. They return `403` on `PATCH` and `DELETE`. Their history comes only from real checks.
- Every other existing service is protected too (`403`), so nothing you registered before can be changed by visitors.
- Visitors can create services (default interval 300 s), and change or delete those visitor services. Caps return `409`, an interval below the minimum returns `422`.

Full reference, including the error order and edge cases: [docs/architecture.md §7](docs/architecture.md#7-demo-mode-spec-003a).

---

## What This Project Demonstrates

| Skill | Where |
|---|---|
| Background job scheduling | `app/workers/scheduler.py` — per-service APScheduler jobs |
| Fault-tolerant status logic | `app/services/monitor.py` + `status.py` — failure thresholds |
| Clean API design | Typed request/response schemas, proper HTTP status codes, 409/404 handling |
| ORM + migrations | SQLAlchemy 2 models, Alembic versioned migrations |
| Time-series data | Indexed `checked_at`, time-range queries, uptime aggregation |
| Resource management | Session lifecycle in background threads, scheduler shutdown on app teardown |
| Docker best practices | Health checks, `depends_on` conditions, `.dockerignore`, startup scripts |
| Configuration management | `pydantic-settings` for type-safe, validated env vars |

---

## Author

**Pedro Flores** — Backend Developer (Python · FastAPI · PostgreSQL)

Portfolio: [github.com/pedrofloresdev](https://github.com/pedrofloresdev)
