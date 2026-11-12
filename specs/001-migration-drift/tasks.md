# 001 - Migration drift: tasks

Status: approved (2026-11-12)
Spec: `spec.md` (approved) · Plan: `plan.md` (approved)

Rules for every task:
- Implement one task, then stop.
- The spec 000 checks stay green after each task: `ruff check .`, `ruff format --check .`, `mypy`, and `pytest` with coverage ≥ 88%.
- "Local PG" means `docker compose up -d db` (with a temporary `.env` copied from `.env.example`) and `TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres`.

## Tasks

- [x] **T1. Schema diff logic, test first.** Make these changes:
  - Write `tests/test_schema_check.py` first (plan §10 RF-7 list).
  - Then add `tools/__init__.py` and the pure functions in `tools/schema_check.py`: `normalize_type`, `snapshot_from_inspector` working on inspector-shaped data, `diff_schemas`, `format_differences`, and the frozen `SchemaSnapshot`.
  - Add `"tools"` to the mypy `files` list and to the isort `known-first-party` list in `pyproject.toml`.

  RF-7
  - Done when:
    - The new unit tests failed before the implementation existed (red) and pass after it.
    - They cover: VARCHAR vs VARCHAR(4), timestamp with vs without time zone, nullability, default, missing and extra tables and columns, FK delete rule, index uniqueness, name-insensitive constraints, and deterministic order.
    - The spec 000 checks are green.

- [ ] **T2. Schema check command.** Add `temporary_database` (unique name, `DROP ... WITH (FORCE)` in `finally`), `run_alembic` (subprocess, repo-root cwd, `DATABASE_URL` env) and `main()` with exit codes 0, 1 and 2. Make it runnable as `python -m tools.schema_check`. RF-7
  - Done when:
    - Without `TEST_POSTGRES_URL`, the command exits 2 and prints how to start a local PG.
    - On local PG with the current history, it exits 1 and lists exactly the 7 known differences: `metrics.checked_at` nullable, `metrics.checked_at` default, `metrics.status` type, `metrics.status` nullable, and `services.expected_status`, `check_interval` and `is_active` nullable.
    - No `schema_check_*` database remains afterwards (`\l`).
    - The spec 000 checks are green.

- [ ] **T3. PostgreSQL test infrastructure.** Make these changes:
  - `pytest.ini`: register the `postgres` marker and add `-m "not postgres"` to `addopts`.
  - `tests/postgres/__init__.py`.
  - `tests/postgres/conftest.py` with three fixtures:
    - `pg_admin_url`: errors with instructions if the variable is missing; never skips.
    - `make_database`: creates and drops uniquely named databases.
    - `alembic`: runs a revision against a database and returns the exit code and output.

  RF-9 (infrastructure)
  - Done when:
    - The default `pytest` run collects no `postgres` tests and keeps coverage ≥ 88%.
    - `pytest -m postgres --no-cov` without `TEST_POSTGRES_URL` errors with the instructions (not "skipped").
    - With local PG, a throwaway call of each fixture works and leaves no database behind. The throwaway test is not committed.

- [ ] **T4. Migration tests, red.** Write `tests/postgres/test_migration_drift.py` (`pytestmark = pytest.mark.postgres`) with the tests in plan §10:
  - RF-1, RF-3 (with a non-zero count and the zero case), RF-4, RF-5 (NULL status and over-long status, both with full rollback), RF-6 (downgrade keeps rows; downgrade then upgrade equals a single upgrade).
  - The edge cases: hand-fixed columns, and `upgrade head` run twice.

  Seed through the `alembic` fixture at revision `3f8a12b9c4d1`, using SQL. RF-1, RF-3, RF-4, RF-5, RF-6
  - Done when:
    - With local PG, every new test fails because the migration does not exist yet. The failure output shows the missing behavior, not a fixture error.
    - `ruff` and `mypy` are clean.

- [ ] **T5. The migration.** Create it with `alembic revision -m "align schema with models"` (`down_revision = "3f8a12b9c4d1"`) and hand-write the upgrade and downgrade from plan §7.1 and §7.2, including the offline-mode branch. Existing revisions are not edited. RF-1..RF-6, RNF-1
  - Done when:
    - All T4 tests pass on local PG.
    - `python -m tools.schema_check` exits 0 with "No schema differences".
    - `git diff --stat` shows only one added file under `migrations/versions/`.
    - The spec 000 checks are green.

- [ ] **T6. End-to-end test on the migrated schema.** Write `tests/postgres/test_migrated_schema_api.py`. It migrates a fresh DB to head, stores a service and a check without a timestamp through the ORM, then calls `GET /api/v1/metrics/services/{id}` and `GET /api/v1/status` through `TestClient`, with `get_db` overridden to the PG session and the scheduler patched. RF-2, RF-9
  - Done when:
    - The test passes at head.
    - With the DB migrated only to `3f8a12b9c4d1`, the same test fails: metrics returns 500 or uptime is 0.0. This is checked once by temporarily changing the target revision, and not committed.
    - All `postgres` tests pass.

- [ ] **T7. CI job.** Add the `migrations` job to `.github/workflows/ci.yml` per plan §4.2: a PG15 service with a dummy password, `TEST_POSTGRES_URL`, `python -m tools.schema_check`, then `pytest -m postgres --no-cov`, with the same pinned actions. RF-8, RNF-5
  - Done when:
    - actionlint is clean, and the workflow has no `secrets.` reference.
    - After PR #2 (`feature/001-migration-drift` into `feature/000-foundations`) is merged, which needs owner confirmation, PR #1 shows `migrations` and the other four checks green.
    - The run takes under 5 minutes.

- [ ] **T8. Docs.** Make these changes:
  - README Quality Checks: the local PG command, `TEST_POSTGRES_URL`, the schema check and `pytest -m postgres` commands, five required CI checks, and the backup note for the upgrade.
  - AGENTS.md Commands.
  - `docs/architecture.md` §6 trade-off.

  RF-7
  - Done when:
    - Every new command runs as written: the check exits 0 and the `postgres` tests pass on local PG.
    - The README lists the 5 check names.
    - The architecture doc matches the code.

- [ ] **T9. Validation evidence.** Record the following in `specs/001-migration-drift/validation.md`:
  - RNF-3: seed 1M metrics (half legacy) at `3f8a12b9c4d1` on local PG, then time `alembic upgrade head`. It must take under 60 s and report the deleted count.
  - The spec 000 compose smoke test: a registered service, then the metrics endpoint returns 200 with timestamps, and `uptime_last_24h` is above 0.
  - RNF-2: the OpenAPI snapshot is identical to `feature/000-foundations`.
  - RF-7: a throwaway PR to `master` with a drifted model column shows `migrations` red, naming the column. It is closed unmerged and its link recorded.

  RF-1..RF-9, RNF-1..RNF-5
  - Done when:
    - `validation.md` lists every spec completion criterion with evidence (output summaries, PR and run links).
    - Every item passes, or the failure is stated plainly.

## Coverage check

| Req | Tasks |
|---|---|
| RF-1 | T4, T5, T9 |
| RF-2 | T5, T6 |
| RF-3 | T4, T5, T9 |
| RF-4 | T4, T5 |
| RF-5 | T4, T5 |
| RF-6 | T4, T5 |
| RF-7 | T1, T2, T8, T9 |
| RF-8 | T7 |
| RF-9 | T3, T6, T9 |
| RNF-1 | T5 |
| RNF-2 | T9 |
| RNF-3 | T9 |
| RNF-4 | every task (spec 000 checks) |
| RNF-5 | T7 |
| RNF-6 | none needed (no new dependency or Docker change; plan §12) |
