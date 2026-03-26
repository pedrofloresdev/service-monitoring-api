# Architecture

## Overview
{High-level description of how the system is structured.}

## Directory structure
```
app/
├── api/        ← FastAPI routes (services, metrics, status)
├── core/       ← DB config
├── db/         ← SQLAlchemy models
├── schemas/    ← Pydantic schemas
├── services/   ← Business logic (monitoring, status detection)
├── workers/    ← APScheduler background jobs
└── main.py     ← Entry point
```

## Key design decisions
- Failure threshold logic: mark DOWN only after N consecutive failures
- Background scheduler polls all registered services automatically
- Migrations managed by Alembic

### Decision 001 — {Title}
- **Date:** YYYY-MM-DD
- **Context:** {Why this decision was needed}
- **Decision:** {What was decided}
- **Consequences:** {What this means going forward}

<!-- Add more decisions as they are made -->

## Module responsibilities
| Module | Responsibility |
|--------|---------------|
| {module} | {what it does} |

## External dependencies
| Library | Version | Why |
|---------|---------|-----|
| {lib} | {ver} | {reason} |

## Data models
{Describe key data structures or models here. Update as they evolve.}

## API surface (if applicable)
{List key endpoints or interfaces. Update as they are built.}
