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
- Demo mode (spec 003a, off by default): seeded example services that visitors cannot change (real check history only, immediate first checks), visitor services with a global and per-host cap and a minimum check interval, 403/409/422 responses in a fixed order. With demo mode on or off, a duplicate URL is always 409 and `null` on a required `PATCH` field is 422. A public deployment also needs specs 003b and 003c. Details in `docs/architecture.md` §7.
- Project start command and single process (spec 003d): `python -m app.serve` runs the migrations and the server in every mode (the container runs it directly). With demo mode on it checks `WEB_CONCURRENCY`, validates every demo setting, requires PostgreSQL and takes a PostgreSQL advisory lock before migrating, so exactly one process per database runs demo mode; a heartbeat ends the process if the lock is lost. Details in `docs/architecture.md` §8.

## Out of scope
- Authentication/multi-tenant accounts, alerting/notifications, UI dashboard, non-HTTP checks (TCP, DNS), distributed workers.

## Spec roadmap
- `000-foundations` - baseline: document architecture, add pyproject with ruff + mypy and make them green, keep pytest green, fix `.env.example`, add GitHub Actions CI (lint, format, type check, tests).
- `001-migration-drift` - align the migration history with the models (`metrics.checked_at` default/NOT NULL and other drift) and add a CI check that the migrated schema matches the models. Resolves 000 OQ-1.
- `002-url-safety` - reject service URLs that point to non-public addresses (SSRF), re-validate the destination before every check.
- `003a-demo-mode` - opt-in demo mode: seeded example services (protected, real history only), visitor caps (global, per host), demo minimum interval, duplicate-URL 409 everywhere, PATCH null 422.
- `003b-rate-limiting` - demo mode: per-client sliding-window rate limits, app-owned client identification (trusted proxy hops), per-client visitor cap.
- `003c-demo-reset` - demo mode: periodic UTC-slot reset of visitor data with catch-up and retry, write gate, `GET /` reset notice.
- `003d-demo-lock` - demo mode: project start command (lock before server, migrations after lock) and single-process lock on PostgreSQL.
- Implementation order for 003: 003a -> 003d -> 003c -> 003b. All four are needed before a public demo deployment.
- `004-metrics-retention` - configurable retention/cleanup of old metrics.
- `005-alerting` - notify (webhook) on UP->DOWN and DOWN->UP transitions.
- `006-postgres-test-parity` - run the test suite against PostgreSQL in CI.
- `007-api-auth` - API key protection for write endpoints.
