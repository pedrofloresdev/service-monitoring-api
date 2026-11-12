# Product SPEC - Service Monitoring API

## Vision
A small, reliable uptime monitor exposed as a REST API: register endpoints, check them automatically, and answer "is it up, how fast, how reliable" from stored history. Also a portfolio piece showing clean FastAPI + PostgreSQL backend practice.

## Users
- Developers/operators who want to track the health of their HTTP services.
- Recruiters/reviewers evaluating the codebase as a portfolio project.

## Main flows
1. Register a service (name, URL, expected status code, check interval); it is scheduled immediately.
2. The scheduler checks each active service at its interval and stores a metric (status, status code, response time, error).
3. Query a service's metrics, paginated and optionally limited to the last N hours.
4. Get aggregated status for all active services: UP/DOWN (after N consecutive failures), average response time, 24h uptime.
5. Update, deactivate/reactivate or delete a service; its schedule follows.

## Current scope
- Service CRUD, per-service scheduling, metrics storage and query, aggregated status, liveness endpoint.
- Docker compose setup with Postgres and automatic migrations.
- 50 pytest tests (~88% coverage).

## Out of scope
- Authentication/multi-tenant accounts, alerting/notifications, UI dashboard, non-HTTP checks (TCP, DNS), distributed workers.

## Spec roadmap
- `000-foundations` - baseline: document architecture, add pyproject with ruff + mypy and make them green, keep pytest green, fix `.env.example`, add GitHub Actions CI (lint, format, type check, tests).
- `001-metrics-retention` - configurable retention/cleanup of old metrics.
- `002-alerting` - notify (webhook) on UP->DOWN and DOWN->UP transitions.
- `003-postgres-test-parity` - run the test suite against PostgreSQL in CI.
- `004-api-auth` - API key protection for write endpoints.
