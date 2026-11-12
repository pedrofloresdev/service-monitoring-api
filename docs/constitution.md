# Constitution - Service Monitoring API

Non-negotiable principles. Specs, plans and code must respect them. Changing one requires explicit approval from the owner.

1. **Correct health data first.** Status, uptime and response times must reflect real checks; never fabricate, drop or silently alter a metric.
2. **Spec is the source of truth.** Not in the spec = not built.
3. **Config-driven.** Thresholds, timeouts and intervals live in settings loaded from the environment.
4. **Pure logic separated and tested.** Status rules and aggregation are functions with tests written first.
5. **Files are the source of truth.** Schema changes only through Alembic migrations; nothing configured by hand in a DB console.
6. **Monitoring must not break the API.** A failing or slow check never crashes the scheduler or blocks request handling; every check has a timeout.
7. **Secure by default.** No secrets in code or settings defaults; validate all input through schemas.
8. **English and strict.** Everything in English; type hints on all functions.
9. **Small steps.** One task at a time, each verifiable, each committed.
