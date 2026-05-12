# Service Monitoring API

A production-ready backend system that monitors external and internal services, tracks uptime, and delivers real-time observability through automated health checks and performance metrics.

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
pip install -r requirements.txt

# Set DATABASE_URL in .env, then:
alembic upgrade head
uvicorn app.main:app --reload
```

---

## Configuration

All settings are loaded from `.env` via `pydantic-settings`.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(required)* | PostgreSQL connection string |
| `SCHEDULER_INTERVAL_SECONDS` | `60` | Fallback check interval if not set per service |
| `FAIL_THRESHOLD` | `3` | Consecutive failures before marking a service DOWN |
| `CHECK_TIMEOUT_SECONDS` | `10` | HTTP request timeout per check |

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
