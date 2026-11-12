# 001 - Migration drift: validation notes

Status: in progress (local evidence complete; GitHub evidence pending: CI on PR #1 and the throwaway drift PR)
Branch: `feature/001-migration-drift` (stacked on `feature/000-foundations`). Environment: Python 3.11.18 venv from `requirements-dev.txt`, PostgreSQL 15 (compose `db`), Docker 29.2.1 / Compose v2.40.3 (WSL). Date: 2026-11-12.

## Completion criteria

| Criterion | Result | Evidence |
|---|---|---|
| Empty PostgreSQL: `upgrade head` then the RF-7 check reports no difference | PASS | `python -m tools.schema_check` → "No schema differences between the migration history and the models.", exit 0. Before the migration, the same command listed exactly the 7 known differences, exit 1. |
| PostgreSQL built from the old history with legacy metrics, NULL services and valid checks: migration applies, legacy deleted with correct count, valid data intact, RF-7 passes | PASS | `test_legacy_metrics_deleted_and_counted` ("Deleted 3 legacy metric(s)", 2 valid remain), `test_zero_legacy_metrics_reports_zero`, `test_service_nulls_backfilled_with_model_defaults` ((1,200,60,True),(2,201,30,False)), `test_full_history_matches_models` |
| RF-5 abort and RF-6 downgrade work on a seeded database | PASS | `test_bad_status_aborts_and_changes_nothing[null-status|long-status]`: non-zero exit, message "1 metrics row(s) have a NULL or >4-character status", version still `3f8a12b9c4d1`, schema snapshot unchanged, all 6 rows present (delete rolled back). `test_downgrade_restores_definitions_and_keeps_rows`, `test_downgrade_then_upgrade_equals_single_upgrade`. |
| RF-9 test passes; compose smoke test shows timestamps, metrics 200, uptime > 0 | PASS | `test_check_without_timestamp_gets_one`, `test_metrics_and_status_endpoints_on_migrated_schema` pass at head and **fail on the pre-001 schema** (`checked_at` None; `MetricsPage.items.0.checked_at` validation error), checked once by temporarily targeting `3f8a12b9c4d1`. Compose (`docker compose up --build`): 4 migrations applied ("Deleted 0 legacy metric(s)"), service registered (201), after 23 s `GET /api/v1/metrics/services/1` → 200 with `checked_at` 20:35:35Z and 20:35:45Z, `GET /api/v1/status` → `uptime_last_24h: 100.0`, `/health` → 200. |
| CI shows the new status green; a drifted model change on a throwaway PR turns it red | PENDING | Needs push and PR (owner confirmation). Workflow: actionlint 1.7.7 clean, no `secrets.` references. |
| Spec 000 checks still green, OpenAPI unchanged | PASS | `ruff check .` clean; `ruff format --check .` 61 files formatted; `mypy` no issues in 37 files (now incl. `tools/`); `pytest` 71 passed, 12 deselected, coverage 88.41%. `app/` identical to `feature/000-foundations`; OpenAPI JSON byte-identical (743 lines). |

## Non-functional requirements

| Req | Result | Evidence |
|---|---|---|
| RNF-1 existing revisions not edited | PASS | `git diff --stat feature/000-foundations -- migrations/` → only `a62134b6f191_align_schema_with_models.py` added (98 lines) |
| RNF-2 API unchanged | PASS | See above (OpenAPI byte-identical) |
| RNF-3 1,000,000 checks < 60 s | PASS | Compose PG15: seeded 1,000,000 metrics (500,000 with NULL `checked_at`) at `3f8a12b9c4d1`; `alembic upgrade head` took **2.62 s** wall-clock including process start, logged "Deleted 500000 legacy metric(s) with NULL checked_at"; afterwards 500,000 rows, 0 NULL |
| RNF-4 spec 000 checks green | PASS | See above |
| RNF-5 CI < 5 min | PENDING | Needs the CI run |
| RNF-6 no new dependency / Docker change | PASS | `requirements*.txt`, `Dockerfile`, `docker-compose.yml` unchanged |

## Other checks

- `pytest -m postgres --no-cov`: 12 passed (about 21 s locally).
- Without `TEST_POSTGRES_URL`: `python -m tools.schema_check` exits 2 with setup instructions; `pytest -m postgres` errors at fixture setup (not skipped).
- No `schema_check_*` / `pytest_pg_*` databases left behind after runs.
- Offline mode: `alembic upgrade 3f8a12b9c4d1:head --sql` renders the DELETE, UPDATEs and ALTERs.
