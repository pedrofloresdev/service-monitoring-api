# 001 - Migration drift

Status: approved (2026-11-12)
Resolves: spec 000, Open question OQ-1. Must be implemented before spec 000 is signed off.

## Context and goal
The database built by applying the full migration history differs from the schema the persistence models declare. The test suite builds its tables from the models, so it never sees the difference. On a real PostgreSQL database built from the migrations:
- The check timestamp has no default and allows NULL. The app relies on the database to set it, so **every stored check has no timestamp**.
- As a result, the metrics endpoint returns 500, 24h uptime is always 0.0, and newest-first ordering is undefined. This breaks constitution principle 1 ("Correct health data first").

Measured on 2026-11-12: migrated PostgreSQL 15 compared with the models.

| Table.column | Migrated database | Models |
|---|---|---|
| `metrics.checked_at` | nullable, no default | not null, default = current time |
| `metrics.status` | nullable, unbounded text | not null, max 4 characters |
| `services.expected_status` | nullable | not null |
| `services.check_interval` | nullable | not null |
| `services.is_active` | nullable | not null |

Everything else matches: types, keys, unique constraint, foreign key with cascade, and indexes.

Goal: a database built from the migration history matches the models exactly. Existing databases are brought to the same state without fabricating health data. An automated check fails whenever the two diverge again.

## Users
- **Owner/developer:** wants the deployed API to return correct metrics and uptime, and wants drift caught before merge.
- **Operator of an existing deployment:** wants to upgrade by applying migrations, with no manual SQL.

## User stories
- As the developer, after applying all migrations to an empty database and running checks, the metrics endpoint returns the stored checks with their timestamps, and 24h uptime reflects them.
- As an operator, I apply the new migration to my existing database and it ends up with the same schema as a fresh one.
- As the developer, if a model change is merged without a matching migration, or the reverse, an automated check fails and names the difference.

## Definitions
- **Migrated schema:** the schema of an empty PostgreSQL database after applying the full migration history.
- **Model schema:** the schema the persistence models declare.
- **Schema difference:** any difference between the two in tables, columns, column types (including string length), nullability, server defaults, primary keys, unique constraints, foreign keys (including the delete rule) or indexes.
- **Legacy metric:** a stored check whose timestamp is NULL, written before this spec.

## Functional requirements
- RF-1: THE SYSTEM SHALL provide a new migration, appended to the history, after which the migrated schema has no schema difference with the model schema.
- RF-2: WHEN a check is stored on a migrated database without an explicit timestamp, THE SYSTEM SHALL record the database's current time as its timestamp.
- RF-3: WHEN the new migration is applied to a database that contains legacy metrics, THE SYSTEM SHALL delete them before enforcing NOT NULL, and SHALL report in the migration output how many were deleted (including when the count is 0). Afterwards, no stored check SHALL have a NULL timestamp. (Owner-approved exception to constitution principle 1, see Open questions.)
- RF-4: WHEN the new migration is applied to a database where any of `services.expected_status`, `services.check_interval` or `services.is_active` is NULL, THE SYSTEM SHALL set those values to the model defaults (200, 60, true) before enforcing NOT NULL.
- RF-5: IF the new migration finds a stored check with a NULL status or a status longer than 4 characters, THEN THE SYSTEM SHALL abort the migration with an error that names the offending row count, and SHALL leave the database unchanged. It SHALL NOT invent or truncate a status.
- RF-6: WHEN the new migration is reverted (downgrade), THE SYSTEM SHALL restore the previous column definitions (nullability, default, type) without deleting rows. Legacy metrics deleted by RF-3 are not restored.
- RF-7: THE SYSTEM SHALL provide one documented command that builds the migrated schema on PostgreSQL and fails, naming each schema difference, when it differs from the model schema. It SHALL detect string length differences and server default differences.
- RF-8: WHEN a pull request targets the default branch or a commit is pushed to it, THE SYSTEM SHALL run the RF-7 check in CI as its own named status, against a disposable PostgreSQL instance, with no secrets.
- RF-9: THE SYSTEM SHALL include an automated test that shows, on the migrated schema, that a check stored without a timestamp gets one, and that the metrics endpoint and status endpoint then return that check with a timestamp and a 24h uptime above 0.

## Non-functional requirements
- RNF-1: The existing migration revisions are not edited. The fix is only additive (constitution principle 5).
- RNF-2: No change to API routes, request or response shapes, status codes or status/uptime rules. The OpenAPI document stays byte-identical to the one after spec 000.
- RNF-3: On a database with 1,000,000 stored checks, the new migration completes in under 60 seconds on the containerized PostgreSQL.
- RNF-4: The spec 000 checks stay green: lint, format, strict type check, and tests with the coverage floor of 88. Any new code is checked code (spec 000 definitions).
- RNF-5: The CI run, including the new check, still finishes in under 5 minutes.
- RNF-6: Any new dependency, or a change to Docker or compose files, needs owner approval in the plan phase.

## Edge cases
- Empty database (fresh install): applying the full history including the new migration succeeds, and the RF-7 check passes.
- Database already at the latest migration: the new migration applies once. Applying `upgrade head` again is a no-op.
- Database where an operator already fixed some columns by hand: the migration still succeeds. Setting NOT NULL or a default on a column that already has it does not fail.
- Legacy metrics: deleted and counted in the migration output (RF-3). Their timestamps are never fabricated. The deletion is never silent.
- Legacy metrics are the only rows a service has: after the migration the service has no metrics, so the status endpoint leaves it out until its next check (existing rule).
- A service row with a NULL in a backfilled column: the model default is applied (RF-4). These are configuration values, not health data.
- A stored status that is NULL or longer than 4 characters: the migration aborts and changes nothing (RF-5). The operator fixes the data and retries.
- The RF-7 check runs where no PostgreSQL is available locally: the documented command says how to start a disposable one (for example the compose database service), and does not silently pass.
- The migration is downgraded and then upgraded again: the result equals a single upgrade.

## Out of scope
- Running the whole test suite against PostgreSQL. This spec adds only the migration check and the RF-9 test. Full parity stays in the roadmap spec for PostgreSQL test parity.
- Changing what the API returns for checks, or how uptime is computed.
- Editing existing migration revisions.
- Retention or cleanup of old metrics (roadmap).
- The hardcoded database credentials and the obsolete key in the compose file (known gaps from spec 000).

## Completion criteria
- On an empty PostgreSQL, `upgrade head` and then the RF-7 check report no schema difference.
- On a PostgreSQL built from the old history and filled with legacy metrics, services with NULLs and valid checks, the new migration applies, the legacy metrics are deleted with the correct count reported, the valid checks and services are intact, and the RF-7 check passes.
- The RF-5 abort and the RF-6 downgrade each work on a seeded database.
- The RF-9 test passes, and the end-to-end compose smoke test from spec 000 shows timestamps, a 200 from the metrics endpoint and uptime above 0.
- The CI run shows the new named status green, and a deliberately drifted model change on a throwaway PR turns it red.
- The spec 000 checks are still green, and the OpenAPI document is unchanged.

## Open questions
- **OQ-1: RESOLVED 2026-11-12. The owner chose option (a): delete legacy metrics and report the count. This is an owner-approved, one-time exception to constitution principle 1 ("never ... drop ... a metric"), limited to rows whose timestamp is NULL. Recorded in RF-3.** The original question follows.
- What happens to legacy metrics, which have a NULL timestamp? Every check stored so far on a PostgreSQL deployment is one, and their real time is unknown. Options:
  - (a) **Delete them** in the migration and report how many were deleted. They can't be shown today anyway, because the endpoint fails on them. This drops metrics, so it needs an approved exception to principle 1.
  - (b) **Set their timestamp to the migration time.** That fabricates a timestamp and would put every legacy check into the 24h uptime window, so it conflicts with principle 1.
  - (c) **Keep the column nullable** for those rows only, and add the default for new rows. The schema would then still differ from the models, which contradicts RF-1 and the goal unless the models also allow NULL, and that would change the API response shape (RNF-2).
