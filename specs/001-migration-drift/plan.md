# 001 - Migration drift: technical plan

Status: approved (2026-11-12, including §12: new migration that deletes legacy metrics, new `migrations` CI job)
Spec: `specs/001-migration-drift/spec.md` (approved; OQ-1 resolved as option (a))
Base: branch `feature/001-migration-drift`, stacked on `feature/000-foundations`

## 1. Pre-plan measurements

All measured on 2026-11-12 on throwaway `postgres:15-alpine` containers. No project files were changed.

| Probe | Result | Consequence for the plan |
|---|---|---|
| Alembic `compare_metadata` (with `compare_type`, `compare_server_default`) on the migrated DB vs the models | Reports 5 nullability/default differences, **but misses `VARCHAR` vs `VARCHAR(4)`** | RF-7 cannot rely on autogenerate comparison (D1) |
| SQLAlchemy `inspect()` on two DBs: one migrated, one built with `create_all` from the models | Shows every expected difference, including `VARCHAR(4)` and the `now()` default | Basis for RF-7 (D1) |
| Same probe: `str(column.type)` | Prints `TIMESTAMP` for both `timestamptz` and `timestamp` | Types must be compiled for the PostgreSQL dialect, which gives `TIMESTAMP WITH TIME ZONE` (D2) |
| Same probe: the unique constraint on `services.url` | `uq_services_url` (migration) vs `services_url_key` (PG auto-name from the model) | Compare constraints and indexes by structure, not by name (D3) |
| The migration statements in §7.1, run as SQL on 1,000,000 metrics (half with NULL `checked_at`) | Total ≈ **2.4 s**: delete 0.08 s, `status` type change 1.8 s, the rest < 0.4 s | RNF-3 (< 60 s) met with a wide margin |
| Re-applying `SET NOT NULL` / `TYPE VARCHAR(4)` on columns that already have them | Succeeds, about 3 ms | Hand-fixed databases are fine (spec edge case) |
| Seeding 1,000,000 rows | about 47 s | RNF-3 is measured manually, not in CI (D10) |
| `alembic.ini` logging | Logger `alembic` at INFO level to stderr | The RF-3 deleted count goes through the Alembic logger and shows in normal `alembic upgrade` output (D5) |

## 2. Approach

1. **One hand-written migration** appended after `3f8a12b9c4d1`. It checks the data, deletes legacy metrics with a reported count, backfills the service config columns, then aligns defaults, nullability and types. The downgrade restores the old definitions. (RF-1..RF-6)
2. **A schema check command** `python -m tools.schema_check`. It builds two disposable databases on a PostgreSQL server, one from the migration history and one from the models, and compares normalized inspector snapshots. It fails and names each difference. (RF-7)
3. **PostgreSQL tests** marked `postgres` and deselected by default. They cover the migration behaviors on seeded databases and the end-to-end RF-9 scenario. (RF-1..RF-6, RF-9)
4. **A CI job `migrations`** with a PostgreSQL service container that runs the check and the `postgres` tests. (RF-8)
5. **Docs**: the new command, the new CI status, and a correction to the architecture trade-off. (RF-7 "documented")

No application code (`app/`) changes, so the API and OpenAPI document are unchanged (RNF-2).

## 3. Files

| File | Action | Responsibility | RF / RNF |
|---|---|---|---|
| `migrations/versions/<rev>_align_schema_with_models.py` | create (via `alembic revision -m "align schema with models"`, hand-written body, `down_revision = "3f8a12b9c4d1"`) | Upgrade and downgrade per §7.1 / §7.2 | RF-1..RF-6, RNF-1, RNF-3 |
| `tools/__init__.py` | create | Package marker | none |
| `tools/schema_check.py` | create | Pure functions `normalize_type`, `snapshot_from_inspector`, `diff_schemas`, `format_differences`; I/O helpers `temporary_database`, `run_alembic`; `main()` entry point (§7.3) | RF-7 |
| `tests/test_schema_check.py` | create (default suite, no PostgreSQL) | Unit tests for the pure functions, written first | RF-7, constitution P4 |
| `tests/postgres/__init__.py` | create | Package marker | none |
| `tests/postgres/conftest.py` | create | Fixtures: `pg_admin_url` (fails with instructions when `TEST_POSTGRES_URL` is missing; never skips), `make_database` (creates and drops uniquely named databases), `alembic` helper that runs a target revision against a given database and returns its output | RF-9, test infrastructure |
| `tests/postgres/test_migration_drift.py` | create (`pytestmark = pytest.mark.postgres`) | Migration behaviors on seeded databases (§10) | RF-1..RF-6 |
| `tests/postgres/test_migrated_schema_api.py` | create (`pytestmark = pytest.mark.postgres`) | RF-9 end to end: migrated DB, store a check without a timestamp, call the metrics and status endpoints | RF-2, RF-9 |
| `pytest.ini` | modify | Register marker `postgres`; add `-m "not postgres"` to `addopts` so the default `pytest` run (and coverage gate) is unchanged | RNF-4 |
| `pyproject.toml` | modify | `tool.mypy.files` += `"tools"`; `tool.ruff.lint.isort.known-first-party` += `"tools"` | RNF-4 |
| `.github/workflows/ci.yml` | modify | New job `migrations` (§4.2) | RF-8, RNF-5 |
| `README.md` | modify | Quality Checks: the migration check command, how to start a local PostgreSQL (`docker compose up -d db`), the `TEST_POSTGRES_URL` variable, five required CI checks instead of four. Upgrade note: this migration deletes metrics with no timestamp and logs the count, so back up first. | RF-7, RF-3 |
| `AGENTS.md` | modify | Commands: migration check and `pytest -m postgres` | RF-7 |
| `docs/architecture.md` | modify | §6 trade-offs: tests on SQLite, **plus** the migration history verified against the models on PostgreSQL in CI. §1 data model: no change (it already describes the model schema). | RF-7 |
| `MEMORY.md` | modify | End of each phase | none (process) |

**Not touched:** `app/**`, existing migration revisions (RNF-1), `migrations/env.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `requirements*.txt`. No new dependency: SQLAlchemy, Alembic and psycopg2 are already pinned.

## 4. Configuration

### 4.1 Environment variable (tooling only, not an app setting)

| Variable | Example (local) | Used by | Notes |
|---|---|---|---|
| `TEST_POSTGRES_URL` | `postgresql://postgres:postgres@localhost:5432/postgres` | `tools.schema_check`, `tests/postgres/*` | Admin URL of a PostgreSQL server where databases may be created and dropped. Not read by the app, so not in `.env.example` (spec 000 RF-10). If missing or unreachable, the command exits 2 with instructions, and `postgres` tests error out instead of skipping. |

### 4.2 CI job `migrations` (added to `.github/workflows/ci.yml`)

| Setting | Value |
|---|---|
| job id / name | `migrations` / `migrations` (status `CI / migrations`) |
| `services.postgres` | `image: postgres:15-alpine`, `env: POSTGRES_PASSWORD: postgres` (non-secret dummy), `ports: 5432:5432`, health check `pg_isready` |
| `env` | `TEST_POSTGRES_URL: postgresql://postgres:postgres@localhost:5432/postgres` |
| steps | checkout → setup-python 3.11 with pip cache (same pinned SHAs) → `pip install -r requirements-dev.txt` → `python -m tools.schema_check` → `pytest -m postgres --no-cov` |
| `timeout-minutes` | 10 |
| independence | No `needs:`. Runs in parallel with the matrix jobs, under the same `concurrency` group. |

### 4.3 State changes (database)

| Table.column | Before (migrated) | After | Data step first |
|---|---|---|---|
| `metrics.checked_at` | nullable, no default | NOT NULL, default `now()` | delete rows with NULL (RF-3) |
| `metrics.status` | nullable `VARCHAR` | NOT NULL `VARCHAR(4)` | abort if any NULL or length > 4 (RF-5) |
| `services.expected_status` | nullable | NOT NULL | NULL → 200 (RF-4) |
| `services.check_interval` | nullable | NOT NULL | NULL → 60 (RF-4) |
| `services.is_active` | nullable | NOT NULL | NULL → true (RF-4) |

## 5. Documented commands (added)

| Purpose | Command |
|---|---|
| Start a local PostgreSQL for checks | `docker compose up -d db` |
| Migration history vs models | `TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres python -m tools.schema_check` |
| PostgreSQL tests | `TEST_POSTGRES_URL=... pytest -m postgres --no-cov` |

## 6. Pure logic functions (`tools/schema_check.py`)

| Function | Signature (intent) | Rule |
|---|---|---|
| `normalize_type` | `(type_: TypeEngine) -> str` | `type_.compile(dialect=postgresql.dialect())`, so length, precision and time zone are included |
| `snapshot_from_inspector` | `(inspector) -> SchemaSnapshot` | For every table except `alembic_version`: columns `(name, type, nullable, default)`, primary key columns, unique constraints as sorted column tuples, foreign keys as `(columns, referred_table, referred_columns, ondelete)`, indexes as `(columns, unique)`. Indexes flagged `duplicates_constraint` are excluded. Names are dropped (D3). |
| `diff_schemas` | `(migrated: SchemaSnapshot, models: SchemaSnapshot) -> list[str]` | Deterministic, sorted, one human-readable line per difference, for example `metrics.status: type VARCHAR (migrated) != VARCHAR(4) (models)`. Covers missing or extra tables, missing or extra columns, and each attribute difference. |
| `format_differences` | `(diffs: list[str]) -> str` | Report text, or "No schema differences" when empty |

`SchemaSnapshot` is a frozen dataclass of plain tuples, so it is pure and easy to build in unit tests.

## 7. Algorithms (pseudocode)

### 7.1 Migration upgrade
```
log = logging.getLogger("alembic.runtime.migration")
if offline mode (--sql):
    emit the same statements without the count queries; log "row counts unavailable in offline mode"
else:
    bad = SELECT count(*) FROM metrics WHERE status IS NULL OR length(status) > 4
    if bad > 0:
        raise RuntimeError(f"{bad} metrics row(s) have a NULL or >4-character status; "
                           "fix them and re-run. No changes were made.")      # RF-5
    deleted = DELETE FROM metrics WHERE checked_at IS NULL  -> rowcount
    log.info("Deleted %d legacy metric(s) with NULL checked_at", deleted)      # RF-3 (also 0)
UPDATE services SET expected_status = 200  WHERE expected_status IS NULL       # RF-4
UPDATE services SET check_interval  = 60   WHERE check_interval  IS NULL
UPDATE services SET is_active       = true WHERE is_active       IS NULL
ALTER metrics.checked_at  SET DEFAULT now(), SET NOT NULL                       # RF-1, RF-2
ALTER metrics.status      TYPE VARCHAR(4), SET NOT NULL                         # RF-1
ALTER services.expected_status, check_interval, is_active SET NOT NULL          # RF-1
```
PostgreSQL DDL is transactional and Alembic wraps each revision in a transaction, so an abort (RF-5) or any failure rolls back everything, including the delete. All statements are safe on columns that already have the target definition (spec edge case, measured in §1).

### 7.2 Migration downgrade (RF-6)
```
ALTER services.expected_status, check_interval, is_active DROP NOT NULL
ALTER metrics.status     TYPE VARCHAR, DROP NOT NULL
ALTER metrics.checked_at DROP DEFAULT, DROP NOT NULL
# no row is deleted; legacy metrics deleted on upgrade are not restored (spec RF-6)
```

### 7.3 Schema check (`python -m tools.schema_check`, RF-7)
```
url = env TEST_POSTGRES_URL
if missing or unreachable: print instructions (start `docker compose up -d db`, set the variable); exit 2
with temporary_database(url, "schema_check_migrated_<hex>") as migrated_url,
     temporary_database(url, "schema_check_models_<hex>")   as models_url:
    run_alembic(["upgrade", "head"], DATABASE_URL=migrated_url)   # subprocess, cwd = repo root (D4)
    create_engine(models_url); Base.metadata.create_all(engine)    # imports app.db.models.*
    diffs = diff_schemas(snapshot(inspect(migrated)), snapshot(inspect(models)))
print(format_differences(diffs))
exit 1 if diffs else 0
# temporary_database: CREATE DATABASE (AUTOCOMMIT) on enter; DROP DATABASE ... WITH (FORCE) on exit, always
```

## 8. Rendering / UI behavior

- **API:** none. `app/` is unchanged.
- **CI:** a fifth status, `CI / migrations`, appears on PRs, independent of the other four.
- **Command output:** one line per difference plus a count, exit 1. "No schema differences", exit 0. Setup problem, exit 2.
- **`alembic upgrade head` output** gains `INFO [alembic.runtime.migration] Deleted N legacy metric(s) with NULL checked_at`.

## 9. Technical decisions

| ID | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | Compare two real databases (migrated vs `create_all`) with the SQLAlchemy inspector | Alembic `compare_metadata` / `alembic check` | Measured: autogenerate misses the `VARCHAR(4)` length difference, which RF-7 requires. |
| D2 | Normalize types by compiling for the PostgreSQL dialect | `str(type)` | `str()` hides `WITH TIME ZONE` (measured), so a timezone drift would slip through. |
| D3 | Compare constraints and indexes by structure (columns, uniqueness, FK target, delete rule), ignoring names | Rename the model's unique constraint to `uq_services_url` | Names are not in the spec's definition of a schema difference. Renaming would change model DDL with no user-facing benefit. |
| D4 | Run Alembic as a subprocess with `DATABASE_URL` set | `alembic.command.upgrade` in-process | `migrations/env.py` reads `DATABASE_URL` from the process environment and overrides the config URL, so in-process runs would need global env mutation. A subprocess also captures the RF-3 log line for tests. |
| D5 | Report the deleted count through the Alembic logger | `print()` | Matches the existing Alembic output format and level, and goes to stderr like the other migration lines. |
| D6 | One migration for all drift | One migration per column | One atomic change; the downgrade is symmetric. |
| D7 | Separate CI job `migrations` with a PostgreSQL service | Add PostgreSQL to every matrix job | Only one job needs a database; the other checks stay fast and DB-free. |
| D8 | `postgres` marker, deselected by default via `addopts` | Separate test directory outside `testpaths` | `mypy` and `ruff` still check the tests. The default `pytest` and coverage gate stay unchanged. Running them explicitly without a database errors instead of skipping. |
| D9 | Hand-written migration | `alembic revision --autogenerate` | Autogenerate misses the length change and cannot write the data steps (RF-3..RF-5). |
| D10 | RNF-3 (1M rows < 60 s) measured manually and recorded in validation | A CI test | Seeding 1M rows takes about 47 s, which would eat most of the 5-minute CI budget (RNF-5). The migration itself takes about 2.4 s. |
| D11 | Unique database names per run plus `DROP ... WITH (FORCE)` in `finally` | Fixed names | Parallel or aborted runs never collide or leave a database behind. |
| D12 | Stack 001 on `feature/000-foundations`: PR #2 targets `feature/000-foundations`, then PR #1 (000+001) goes to `master` | Separate PRs to `master` | 000 cannot be signed off without 001 (OQ-1). CI only triggers on PRs to `master` (spec 000 RF-5), so the full CI for 001 runs on PR #1 after PR #2 merges. The throwaway drift PR targets `master` to trigger CI. |

## 10. Test strategy

Logic is test-first (constitution P4). The pure functions in `tests/test_schema_check.py` are written before `tools/schema_check.py`.

| Req | Automated test (file) | Manual check |
|---|---|---|
| RF-1 | `tools.schema_check` exits 0 on a fresh server; `test_full_history_matches_models` (`test_migration_drift.py`) calls the same functions | |
| RF-2 | `test_check_without_timestamp_gets_one` (`test_migrated_schema_api.py`) | |
| RF-3 | `test_legacy_metrics_deleted_and_counted`: upgrade to `3f8a12b9c4d1`, seed 3 legacy + 2 valid metrics, upgrade head, assert 2 remain and the output contains "Deleted 3 legacy metric(s)". Also `test_zero_legacy_reports_zero`. | |
| RF-4 | `test_service_nulls_backfilled_with_model_defaults` | |
| RF-5 | `test_null_status_aborts_and_changes_nothing`, `test_long_status_aborts_and_changes_nothing`: upgrade exits non-zero, the message names the count, columns are still nullable, all rows present (including the legacy ones, proving rollback) | |
| RF-6 | `test_downgrade_restores_definitions_keeps_rows`; `test_downgrade_then_upgrade_equals_single_upgrade` (snapshot equality) | |
| RF-7 | `tests/test_schema_check.py`: type normalization (VARCHAR vs VARCHAR(4), timestamp with/without tz), nullability, default, missing/extra table and column, FK delete rule, index uniqueness, name-insensitivity, deterministic order. CLI exit 2 without `TEST_POSTGRES_URL`. | Throwaway PR to `master` with a drifted model column: `CI / migrations` red, naming the column. Closed unmerged. |
| RF-8 | | PR shows `CI / migrations` green, with no secrets in the workflow |
| RF-9 | `test_metrics_and_status_endpoints_on_migrated_schema` | Compose smoke test (spec 000 T10 procedure): metrics endpoint 200 with timestamps, uptime > 0 |
| Spec edge: hand-fixed DB | `test_upgrade_succeeds_when_columns_already_fixed` | |
| Spec edge: already at head | `test_upgrade_head_twice_is_noop` | |
| RNF-1 | | `git diff` shows only an added file under `migrations/versions/` |
| RNF-2 | | OpenAPI snapshot identical to `feature/000-foundations` (spec 000 harness) |
| RNF-3 | | Seed 1M metrics (half legacy) on the compose DB at revision `3f8a12b9c4d1`, time `alembic upgrade head`: < 60 s |
| RNF-4 | `ruff check .`, `ruff format --check .`, `mypy` (now including `tools`), `pytest` (coverage ≥ 88, unchanged because `app/` is untouched) | |
| RNF-5 | | CI run duration < 5 min |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Deleting legacy metrics on an existing deployment is irreversible | Owner-approved (spec OQ-1). The count is always logged. README upgrade note recommends a backup before `alembic upgrade head`. |
| A future model change adds a feature the inspector snapshot ignores (for example check constraints or comments) | `diff_schemas` covers everything in the spec's definition. Extending it is a spec change. |
| `postgres` tests are slow because each creates databases | Each test uses its own small database. Expected well under 1 minute in CI. |
| Local runs without Docker | The command and the fixtures fail with instructions, never silently pass (spec edge case) |

## 12. Approvals needed

1. **Schema/migration change:** a new Alembic revision that **deletes legacy metrics** on existing databases (AGENTS.md "ask first"; spec OQ-1 approved the deletion policy).
2. **CI change:** new `migrations` job with a PostgreSQL service container.

No new dependency, no Docker or compose change, no file deletion.

## 13. RF coverage matrix

| Req | Plan sections |
|---|---|
| RF-1 | §3, §4.3, §7.1, D6, D9 |
| RF-2 | §4.3, §7.1, §10 (RF-9 test) |
| RF-3 | §4.3, §7.1, §8, D5 |
| RF-4 | §4.3, §7.1 |
| RF-5 | §4.3, §7.1 |
| RF-6 | §7.2 |
| RF-7 | §3, §4.1, §5, §6, §7.3, D1–D4, D11 |
| RF-8 | §4.2, D7 |
| RF-9 | §3, §10 |
| RNF-1 | §3 ("Not touched") |
| RNF-2 | §2, §3 |
| RNF-3 | §1, D10 |
| RNF-4 | §3 (`pyproject.toml`, `pytest.ini`), §10 |
| RNF-5 | §4.2, D7, D10 |
| RNF-6 | §12 |
