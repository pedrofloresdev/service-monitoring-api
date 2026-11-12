# Product SPEC - Service Monitoring API

## Vision
A small, reliable uptime monitor exposed as a REST API: register endpoints, check them automatically, and answer "is it up, how fast, how reliable" from stored history. Also a portfolio piece showing clean FastAPI + PostgreSQL backend practice.

## Users
- Developers/operators who want to track the health of their HTTP services.
- Recruiters/reviewers evaluating the codebase as a portfolio project.

## Main flows
1. Register a service (name, URL, expected status code, check interval); it is scheduled immediately. URLs that point to non-public addresses are rejected.
2. The scheduler checks each active service at its interval and stores a metric (status, status code, response time, error).
3. Query a service's metrics, paginated and optionally limited to the last N hours.
4. Get aggregated status for all active services: UP/DOWN (after N consecutive failures), average response time, 24h uptime.
5. Update, deactivate/reactivate or delete a service; its schedule follows.

## Current scope
- Service CRUD, per-service scheduling, metrics storage and query, aggregated status, liveness endpoint.
- Docker compose setup with Postgres and automatic migrations.
- Quality baseline (spec 000): ruff lint/format, strict mypy, 71 pytest tests with an 88% coverage floor, GitHub Actions CI.
- Migration history verified against the models on PostgreSQL in CI (spec 001).
- URL safety (spec 002): service URLs pointing to non-public addresses (loopback, private, link-local/cloud metadata, reserved ranges, `localhost`) are rejected with 422 on create/update, and every check validates the destination again (DNS rebinding) and connects only to validated public addresses; blocked checks are stored as DOWN with a fixed reason. Details in `docs/architecture.md` §6.

## Out of scope
- Authentication/multi-tenant accounts, alerting/notifications, UI dashboard, non-HTTP checks (TCP, DNS), distributed workers.

## Spec roadmap
- `000-foundations` - baseline: document architecture, add pyproject with ruff + mypy and make them green, keep pytest green, fix `.env.example`, add GitHub Actions CI (lint, format, type check, tests).
- `001-migration-drift` - align the migration history with the models (`metrics.checked_at` default/NOT NULL and other drift) and add a CI check that the migrated schema matches the models. Resolves 000 OQ-1.
- `002-url-safety` - reject service URLs that point to non-public addresses (SSRF), re-validate the destination before every check.
- `003-metrics-retention` - configurable retention/cleanup of old metrics.
- `004-alerting` - notify (webhook) on UP->DOWN and DOWN->UP transitions.
- `005-postgres-test-parity` - run the test suite against PostgreSQL in CI.
- `006-api-auth` - API key protection for write endpoints.
