# 000 - Foundations: validation notes

Status: validated 2026-11-12. Reviewer subagent: SPEC FULFILLED EXCEPT RF-6; RF-6 then verified after PR #1 merged (see below). **Spec fulfilled.** OQ-1 fixed by spec 001 (merged into this branch via PR #2). All tasks done. Ready for `/sdd-validate`.
Branch: `feature/000-foundations`. Environment: Python 3.11.18 venv from `requirements-dev.txt`, Docker 29.2.1 / Compose v2.40.3 (WSL).

## Completion criteria

| Criterion | Result | Evidence |
|---|---|---|
| Lint, format check, strict type check, tests (coverage ≥ floor) pass locally with the documented commands | PASS | Re-run 2026-11-12 on the merged branch (000 + 001, HEAD `2b16ce8`): `ruff check .` → "All checks passed!"; `ruff format --check .` → "62 files already formatted"; `mypy` → "Success: no issues found in 37 source files" (`app`, `tests`, `tools`); `pytest` → 71 passed, 12 deselected (`postgres` marker, spec 001), "Required test coverage of 88.0% reached. Total coverage: 88.41%". (Before spec 001 was merged: 50 files, 30 typed files, 50 tests, 88.41%.) |
| PR to `master` shows each check as a separate passing status, run < 5 min | PASS | PR #1 (draft), run 43287075723 (`pull_request`): `lint` 14s, `format` 28s, `typecheck` 22s, `test` 19s, all pass; whole run 32s (19:08:29 to 19:09:01 UTC). Workflow also actionlint 1.7.7 clean, no `secrets.`/`DATABASE_URL`. |
| Throwaway PR with a lint error: only `lint` fails, others complete; closed unmerged | PASS | PR #3 (closed unmerged, branch deleted), run 43309663530 on `04063cb` (unused `import os` in `app/main.py`): `lint` fail (F401 `app/main.py:2`); `format`, `typecheck`, `test`, `migrations` completed and passed. |
| Superseded PR runs are cancelled (RF-8) | PASS | PR #3: run 43309941986 on `d7ba7d3` **cancelled** when `4906ff2` was pushed 4 s later (run 43309962391). |
| RF-6: push to `master` runs the same checks | PASS | PR #1 merged 2026-11-12T22:00:32Z as `503610b`; push run 43315795094 on `503610b`: `lint`, `format`, `typecheck`, `test`, `migrations` all success, 39 s (22:00:36 to 22:01:15 UTC). |
| CI after spec 001 merged: all checks green | PASS | PR #1 run 43309568403 on `aad8751`: `lint`, `format`, `typecheck`, `test`, `migrations` all pass; whole run 42 s. |
| `.env.example` matches exactly the settings the app and migration runner read; unused interval setting gone | PASS | Keys `CHECK_TIMEOUT_SECONDS DATABASE_URL FAIL_THRESHOLD` = `Settings.model_fields`; `migrations/env.py` reads only `DATABASE_URL`; `grep SCHEDULER_INTERVAL_SECONDS` matches only `specs/` and `MEMORY.md` |
| Production image does not contain the dev tools | PASS | `docker compose run --rm --no-deps api pip show pytest pytest-cov` → "WARNING: Package(s) not found: pytest, pytest-cov" |
| Architecture doc exists, linked from README, matches the code; docs list the real commands | PASS | `docs/architecture.md`; both Mermaid diagrams confirmed rendering on GitHub by the owner (2026-11-12); rules re-checked against `app/services/status.py`, `app/services/monitor.py`, `app/workers/scheduler.py`, APScheduler `IntervalTrigger` start date. AGENTS.md/README commands all exit 0 as written. |
| RNF-1 (a) OpenAPI byte-identical | PASS | Sorted-key JSON from `app.openapi()`, master worktree vs branch, same venv: identical (743 lines) |
| RNF-1 (b) DDL identical, no new migration | PASS | Postgres-dialect `CREATE TABLE`/`CREATE INDEX` plus per-column Python and server defaults: identical (40 lines). `git diff master -- migrations/` empty. |
| RNF-1 (c) 50 tests pass, edits mechanical/annotation-only | PASS | `pytest --collect-only -qq`: all 50 master node ids are present and pass unchanged (the merged branch collects 71 by default; the 21 extra are spec 001's `tests/test_schema_check.py`, plus 12 `postgres` tests when selected). `tests/` diff: only imports, signatures, `cast()` in `_fake_service`, formatting. |
| RNF-1 (d) clean compose build, `/health` and `/api/v1/status` respond | PASS | `docker compose up --build`: the 3 original migrations applied on PostgreSQL 15 (after spec 001: 4, ending with `a62134b6f191`), container healthy; `GET /health` → 200 `{"status":"ok"}`; `GET /api/v1/status` → 200 |
| RNF-2 mechanical + annotation-only in scheduler/session/check/timestamp/status code | PASS | Diff review of `app/services`, `app/workers`, `app/db/session.py` vs master |

## Finding: OQ-1 (pre-existing bug; fixed by spec 001)

End-to-end smoke test on the compose stack: registered a service checking its own `/health` every 10 s and waited ~23 s.
- Scheduler stored 5 metrics, `status` endpoint showed the service UP, average response time 32.73 ms. Typed models, sessions and the scheduler work on PostgreSQL.
- **But** every metric row has `checked_at = NULL`; `GET /api/v1/metrics/services/1` → **500** (`MetricsPage.items.N.checked_at: Input should be a valid datetime, input_value=None`); `uptime_last_24h` → **0.0**.
- Cause: the migration history never gives `metrics.checked_at` a server default or NOT NULL (the model does). Migrations are unchanged on this branch, so the bug is on `master` too. Details in `spec.md` → Open questions → OQ-1.

The stack and its volume were removed after the check (`docker compose down -v`); the temporary `.env` was deleted.
