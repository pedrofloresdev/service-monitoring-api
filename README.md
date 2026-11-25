# Service Monitoring API

A backend system that monitors publicly reachable HTTP services, tracks uptime, and delivers real-time observability through automated health checks and performance metrics.

---

## Overview

**Service Monitoring API** simulates a real-world observability platform. Register any publicly reachable HTTP endpoint and the system will continuously probe it on its own schedule, record response times, detect failures intelligently, and expose aggregated health data through a clean REST API.

> **Note:** there is no authentication yet (planned in the spec roadmap). Anyone who can reach the API can create, change and delete services, so don't expose it publicly as is.

---

## Key Features

### Automated Health Checks

- Background scheduler (APScheduler) runs checks per service on its own configurable interval
- Each check records HTTP status code and response time
- `httpx` with configurable timeouts; checks run in scheduler background threads, so a slow target never blocks request handling

### Intelligent Status Detection

- Marks a service DOWN only after N consecutive failures (configurable `FAIL_THRESHOLD`)
- Avoids false positives from transient network blips
- Status reverts to UP as soon as a successful check is recorded

### Per-Service Scheduling

- Each service has its own `check_interval` (10s – 86400s)
- Scheduler jobs are created, updated, and removed dynamically as services are registered, updated, or deleted

### Metrics Collection & Aggregation

- Stores response time (`Float` precision) and HTTP status per check
- Time-range filtering (`?hours=24`) and `limit`/`offset` pagination on the metrics endpoint (newest first)
- Uptime percentage calculated over the last 24 hours

### Full CRUD for Services

- Register, list, update (partial PATCH), and delete monitored services
- Deactivating a service (`is_active: false`) removes it from the scheduler without deleting its history
- Duplicate URL detection with 409 Conflict when a service is created

### URL Safety (SSRF Protection)

- Service URLs that point to non-public addresses (loopback, private networks, link-local and cloud metadata, reserved ranges, `localhost`) are rejected with 422
- Every check validates the destination again (protects against DNS rebinding) and connects only to validated public addresses
- Full rules in [docs/architecture.md](docs/architecture.md) §6

### Infrastructure

- Docker + Docker Compose with PostgreSQL health-check and proper `depends_on` condition
- Alembic migrations run automatically on container startup
- Docker `HEALTHCHECK` on the API container
- Consistent logging (timestamp, level, logger name) across all layers

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
├── services/        # Business logic (monitor, status aggregation, URL safety)
├── workers/         # APScheduler lifecycle management
└── main.py          # App factory + lifespan
migrations/          # Alembic revisions
tools/               # schema_check: migration history vs models
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

A URL that points to a non-public address returns **422**. A URL already used by another service returns **409** on create.

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

**Query params:** `limit` (1–1000, default 100) · `offset` (≥ 0, default 0) · `hours` (1–8760, restrict to the last N hours). Results are ordered newest first.

---

### Status

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/status` | Aggregated health for all active services that have at least one check |

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
git clone https://github.com/pedrofloresdev/service-monitoring-api.git
cd service-monitoring-api

cp .env.example .env       # review and adjust if needed

docker compose up --build
```

The container runs `python -m app.serve`, which applies the migrations and then starts the server. API is available at `http://localhost:8000`.

Interactive docs: `http://localhost:8000/docs`

---

### Without Docker

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt               # runtime deps + dev tools (Python 3.11)

cp .env.example .env    # then set DATABASE_URL to your PostgreSQL
python -m app.serve     # runs the migrations, then the server
```

For development with auto-reload (demo mode off only): `alembic upgrade head && uvicorn app.main:app --reload`.

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

GitHub Actions (`.github/workflows/ci.yml`) runs five independent jobs on every pull request to `master` and on every push to `master`. Each job reports its own status: `lint`, `format`, `typecheck`, `test` and `migrations`. The `migrations` job runs the PostgreSQL checks against a disposable PostgreSQL 15 container. All five are required status checks on `master` (branch protection ruleset), so a pull request can't be merged while any of them fails.

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
| `PORT` | `8000` | Port the server listens on, read by `python -m app.serve` (1–65535) and by the Dockerfile `HEALTHCHECK` |
| `WEB_CONCURRENCY` | `1` | Number of server worker processes, read by uvicorn as before. Must be 1 (or unset) in demo mode |

With `docker compose`, the API container's `DATABASE_URL` is set in `docker-compose.yml` and overrides the value from `.env`.

---

## Running a public demo

> **Not ready for public use yet.** Demo mode gives you protected example services and capped visitor writes (spec 003a) in a single enforced process (spec 003d). A public deployment also needs rate limiting (spec 003b) and a periodic reset of visitor data (003c), which are not implemented yet.

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

### One process only

Demo mode must be started with `python -m app.serve` (the container does this); any other start, auto-reload or more than one worker is refused. The start command takes a PostgreSQL lock before migrating, so only one process per database runs demo mode:

- A second process waits up to `DEMO_LOCK_WAIT_SECONDS` for the first to stop, then refuses. On platforms that start the new version before stopping the old one, use a stop-first (recreate) deploy.
- `WEB_CONCURRENCY` must be 1 or unset (some platforms set it), and the database must be PostgreSQL reached directly, not through a transaction-pooling proxy.
- If the lock is lost (database restart, network failure), the process exits with status 1 within the heartbeat timeout plus 2 seconds and the platform restarts it. The old process may overlap a new one by up to heartbeat interval + timeout + 2 s.
- Worst-case startup is about 157 s with the defaults; the container's healthcheck start period is 180 s.

| Variable | Default | Description |
|---|---|---|
| `DEMO_LOCK_WAIT_SECONDS` | `60` | How long a second process waits for the lock (0–3600; 0 = one attempt) |
| `DEMO_LOCK_HEARTBEAT_SECONDS` | `10` | How often the lock is confirmed (1–300) |
| `DEMO_LOCK_TIMEOUT_SECONDS` | `5` | Heartbeat timeout (1–60, below the heartbeat interval) |
| `DEMO_LOCK_KEEPALIVE_SECONDS` | `30` | The database notices a dead lock connection within this time (10–600) |
| `DEMO_LOCK_NAME` | `service-monitoring-api-demo` | Lock name; use different names when staging and production share a database |

Full reference, including messages and exit statuses: [docs/architecture.md §8](docs/architecture.md#8-single-process-and-start-command-spec-003d).

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
| Docker best practices | Health checks, `depends_on` conditions, `.dockerignore`, migrations on container start |
| Security | SSRF protection: non-public destinations rejected on registration and blocked before every check |
| Quality and process | Strict typing, linting, coverage floor, CI with five required checks, Spec-Driven Development |
| Configuration management | `pydantic-settings` for type-safe, validated env vars |

---

## Author

**Pedro Flores** — Backend Developer (Python · FastAPI · PostgreSQL)

Portfolio: [github.com/pedrofloresdev](https://github.com/pedrofloresdev)
