# 000 - Foundations: technical plan

Status: approved (2026-11-12, including the §12 approvals: ruff 0.17.2, mypy 2.4.1, moving pytest/pytest-cov to dev, new CI workflow)
Spec: `specs/000-foundations/spec.md` (approved, no open questions)

## 1. Pre-plan measurements (facts this plan relies on)

All measured on 2026-11-12 against unmodified `master` code, in a throwaway Python 3.11.18 venv with `requirements.txt` installed. No project files changed.

| Item | Result |
|---|---|
| Test suite | 50 passed |
| Coverage of `app/` | 281 of 319 statements = **88.09%** |
| **Coverage floor** (spec Definitions) | **88** |
| `ruff check` (rule set in §4.1) | 18 findings: 15 × I001 import order, 2 × F401 unused import (`MetricResponse` in `app/api/v1/metrics.py`, `pytest` in `tests/test_services.py`), 1 × W292. All auto-fixable, all mechanical. |
| `ruff check` B008 (framework defaults) | 7 findings (`Depends`/`Query`). Cleared by declaring them allowed in config, with no code change. |
| `ruff format --check` (line length 100) | 7 app files and 5 test files reformatted. Query chains in `services/status.py` and `services/monitor.py` stay as written. |
| `mypy --strict` + pydantic plugin | 19 errors in `app/`, 62 in `tests/`. All are missing annotations or typing limitations (`declarative_base()` is `Any`, so model attributes are `Column[int]`; implicit Optional; bare `dict`; untyped test helper). **No real bug found.** |
| Prototype (scratch copy only): typed `DeclarativeBase` + `Mapped[...]` models, route return annotations | Postgres DDL **byte-identical**. OpenAPI JSON **byte-identical**. 50 tests pass. Only 7 trivial mypy errors left in `app/`. |
| Prototype variant: `root()`/`health()` annotated `-> dict[str, str]` *without* `response_model=None` | OpenAPI **changes** (adds an object schema for both routes). This confirms the spec edge case, and `response_model=None` is required. |

## 2. Approach

Five independent pieces, each mapped to RFs:

1. **Tool configuration**: one `pyproject.toml` holding ruff, mypy and coverage config, plus a separate `requirements-dev.txt` (RF-1..4, RNF-4, RNF-5).
2. **Make the code clean**: run the formatter and lint autofix (mechanical), then make annotation-only changes until strict mypy is clean (RF-1..3, RNF-1, RNF-2).
3. **CI**: one GitHub Actions workflow with four independent jobs, so there are four named statuses (RF-5..9, RNF-3).
4. **Configuration hygiene**: remove `SCHEDULER_INTERVAL_SECONDS` and rewrite `.env.example` (RF-10, RF-11).
5. **Documentation**: `docs/architecture.md`, plus updates to README, AGENTS.md and the roadmap (RF-12, RF-13).

No pure domain logic is added or changed by this spec (see §6).

## 3. Files

| File | Action | Responsibility | RF / RNF |
|---|---|---|---|
| `pyproject.toml` | create | Tool config only (no `[project]` table): `[tool.ruff]`, `[tool.ruff.lint]`, `[tool.ruff.format]`, `[tool.mypy]`, `[tool.coverage.*]` | RF-1, RF-2, RF-3, RF-4 |
| `requirements-dev.txt` | create | `-r requirements.txt` plus the pinned dev tools (§4.2) | RNF-4, RNF-5 |
| `requirements.txt` | modify | Remove the `# Testing` block (`pytest`, `pytest-cov`), which moves to dev | RNF-5 |
| `pytest.ini` | keep, unchanged | Still defines `testpaths` and `--cov=app`. The floor lives in coverage config. | RF-4 |
| `.github/workflows/ci.yml` | create | CI workflow (§4.3, §7.1) | RF-5, RF-6, RF-7, RF-8, RF-9, RNF-3, RNF-4 |
| `.env.example` | rewrite | Exactly `DATABASE_URL`, `FAIL_THRESHOLD`, `CHECK_TIMEOUT_SECONDS` (§4.4) | RF-10, RF-11 |
| `app/core/config.py` | modify | Delete the `SCHEDULER_INTERVAL_SECONDS` field | RF-11 |
| `app/db/base.py` | modify | `Base = declarative_base()` becomes `class Base(DeclarativeBase): pass` (typed declaration) | RF-3 |
| `app/db/models/services.py`, `app/db/models/metrics.py` | modify | `Column(...)` becomes `Mapped[T] = mapped_column(...)` with **the same positional type and every keyword argument kept** (`primary_key`, `index`, `nullable`, `unique`, `default`, `server_default`, `ForeignKey(..., ondelete=...)`). `relationship(...)` gets `Mapped[...]` with the same arguments. Cross-model types are imported under `TYPE_CHECKING`. | RF-3, RNF-1b |
| `app/main.py` | modify | `lifespan(...) -> AsyncIterator[None]`. `root()`/`health()` get `-> dict[str, str]` and `response_model=None` in their decorators. | RF-3, RNF-1a |
| `app/api/v1/services.py`, `metrics.py`, `status.py` | modify | Return annotations (`-> Service`, `-> list[Service]`, `-> None`, `-> MetricsPage`, `-> SystemStatus`). These routes already set `response_model`, or are the 204 delete route (`-> None`), so the OpenAPI document does not change (verified). Remove the unused `MetricResponse` import. | RF-1, RF-3 |
| `app/db/session.py` | modify | `get_db() -> Iterator[Session]` (annotation-only; RNF-2 area) | RF-3, RNF-2 |
| `app/services/monitor.py` | modify | `check_service(...) -> dict[str, Any]`; `limit: int \| None = None` with the default unchanged; format the long `logger.info` call (mechanical) | RF-2, RF-3, RNF-2 |
| `app/services/status.py`, `app/workers/scheduler.py` | modify | Mechanical only: import order and formatting. With typed models they need no annotation changes. | RF-1, RF-2, RNF-2 |
| `app/schemas/*.py`, `app/api/v1/__init__.py` | modify | Mechanical only (import order, trailing newline) | RF-1, RF-2 |
| `tests/conftest.py`, `tests/test_*.py` | modify | Mechanical changes plus annotations: fixtures (`-> Iterator[Session]`, `-> Iterator[TestClient]`, `-> Service`), parameters, `-> None` on every test, and `_fake_service(...) -> Service` returning `cast(Service, SimpleNamespace(...))`. Remove the unused `pytest` import. No assertion, input or expected value changes. | RF-1, RF-2, RF-3, RNF-1c |
| `docs/architecture.md` | create | Architecture document (§8.2) | RF-12 |
| `README.md` | modify | Dev setup with `requirements-dev.txt`, a "Quality checks" section with the four commands, the CI check names to mark as required, a link to `docs/architecture.md`, removal of the `SCHEDULER_INTERVAL_SECONDS` row, and a container-only `PORT` note | RF-11, RF-12, RF-13 |
| `AGENTS.md` | modify | `## Commands`: real lint, format, type check and test commands, plus dev install. Removes the "not set up yet" lines. | RF-13 |
| `docs/SPEC.md` | modify | Roadmap entry for `000-foundations` lists "lint, format, type check, tests" | RF-13 |
| `MEMORY.md` | modify | End of each phase (AGENTS.md rule) | none (process) |

**Not touched:** `migrations/**` (spec: never edited), `Dockerfile`, `docker-compose.yml`, `alembic.ini`, `.dockerignore`, `.gitignore` (it already ignores `.ruff_cache/`, `.mypy_cache/` and `.coverage`). No file is deleted.

## 4. Configuration tables

### 4.1 `pyproject.toml`

| Section | Key | Value | Why |
|---|---|---|---|
| `tool.ruff` | `line-length` | `100` | Decision D3 |
| | `target-version` | `"py311"` | Runtime version |
| | `extend-exclude` | `["migrations"]` | Migration history is excluded (spec Definitions) |
| `tool.ruff.lint` | `select` | `["E", "W", "F", "I", "B"]` | RF-1 families: syntax errors and undefined names (F, E9), unused imports and variables (F401, F841), import order (I), mutable defaults (B006), bare `except` (E722), `== None` (E711) |
| | `ignore` | `["E501"]` | The formatter owns line length. Avoids a formatter/linter conflict (RF-2). |
| `tool.ruff.lint.isort` | `known-first-party` | `["app", "tests"]` | Stable import grouping |
| `tool.ruff.lint.flake8-bugbear` | `extend-immutable-calls` | `["fastapi.Depends", "fastapi.Query"]` | Spec edge case: framework DI defaults are declared allowed, not rewritten |
| `tool.mypy` | `python_version` | `"3.11"` | Runtime version |
| | `strict` | `true` | Strict mode (spec Definitions) |
| | `files` | `["app", "tests"]` | Checked code. The plain `mypy` command needs no arguments. |
| | `plugins` | `["pydantic.mypy"]` | Decision D5 |
| `[[tool.mypy.overrides]]` | `module = ["apscheduler.*"]`, `ignore_missing_imports = true` | | APScheduler 3.x ships no type information. This is a per-library exception (spec edge case), not a global relaxation. |
| `tool.coverage.run` | `source` | `["app"]` | Coverage of application code only |
| `tool.coverage.report` | `fail_under` | `88` | Coverage floor (§1) |
| | `precision` | `2` | Coverage compares the total *after rounding to `precision`*. With the default precision of 0, 87.6% would round to 88 and pass, which breaks "integer part ≥ 88". |
| | `exclude_also` | `["if TYPE_CHECKING:"]` | Spec edge case: lines only the type checker evaluates |

### 4.2 Dependencies

| File | Package | Version | Note |
|---|---|---|---|
| `requirements-dev.txt` | `-r requirements.txt` | | Local and CI use the same declarations (RNF-4) |
| | `ruff` | `==0.17.2` | **NEW, needs approval.** Released 2026-10-22 (latest is 0.17.4, 2026-11-05; I chose a release at least 2 weeks old). Linter and formatter in one tool. |
| | `mypy` | `==2.4.1` | **NEW, needs approval.** Released 2026-10-28 |
| | `pytest` | `==8.3.5` | Moved from `requirements.txt`, same pin |
| | `pytest-cov` | `==6.1.0` | Moved from `requirements.txt`, same pin |
| `requirements.txt` | (pytest lines removed) | | Production image stops installing test tools. `Dockerfile` unchanged (RNF-5). |

No type-stub packages are needed: fastapi, pydantic, SQLAlchemy 2.0, httpx and pytest ship their own types. The only untyped import in checked code is APScheduler (override above). `python-dotenv` is used only in `migrations/`, which is excluded.

### 4.3 CI workflow (`.github/workflows/ci.yml`)

| Setting | Value | RF |
|---|---|---|
| `name` | `CI` | |
| `on.pull_request` | `branches: [master]`, default types (`opened`, `synchronize`, `reopened`). Drafts and fork PRs are included by default. | RF-5 |
| `on.push` | `branches: [master]` | RF-6 |
| `permissions` | `contents: read` | RF-9 |
| `concurrency.group` | `ci-${{ github.event.pull_request.number \|\| github.ref }}` | RF-8 |
| `concurrency.cancel-in-progress` | `${{ github.event_name == 'pull_request' }}` (never cancel runs on `master`) | RF-8 |
| jobs | `lint`, `format`, `typecheck`, `test`: four independent jobs with no `needs:` between them | RF-5, RF-7 |
| each job | `runs-on: ubuntu-latest`, `timeout-minutes: 10`; checkout, then `setup-python` with `python-version: "3.11"` and pip cache keyed on `requirements*.txt`, then `pip install -r requirements-dev.txt`, then one command | RF-9, RNF-3, RNF-4 |
| actions | `actions/checkout`, `actions/setup-python`, each pinned to a full commit SHA of its current major release (resolved at implementation, version in a trailing comment) | RNF-4 |
| secrets / env | none. `DATABASE_URL` is **not** set: the test job uses the in-memory value from `tests/conftest.py`, and no other job imports the app (ruff and mypy are static). | RF-9 |

Commands per job: `lint` runs `ruff check .`; `format` runs `ruff format --check .`; `typecheck` runs `mypy`; `test` runs `pytest`.
Status names shown on the PR: `CI / lint`, `CI / format`, `CI / typecheck`, `CI / test`. The README lists these as the checks to mark required.

### 4.4 `.env.example` (final content, by key)

| Key | Value | Comment line in file |
|---|---|---|
| `DATABASE_URL` | `postgresql://change-me:change-me@localhost:5432/service_monitoring_api` | Required. Used by the app and by `alembic`. `docker compose` overrides it for the api container. |
| `FAIL_THRESHOLD` | `3` | Consecutive failed checks before a service is reported DOWN |
| `CHECK_TIMEOUT_SECONDS` | `10` | HTTP timeout per check, in seconds |

The unused keys `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` and `SCHEDULER_INTERVAL_SECONDS` are removed. `PORT` is container-only and is documented in the README instead (RF-13).

## 5. Documented commands (single source: AGENTS.md and README)

| Purpose | Command |
|---|---|
| Install dev tools | `pip install -r requirements-dev.txt` |
| Lint | `ruff check .` |
| Format (write) / check | `ruff format .` / `ruff format --check .` |
| Type check | `mypy` |
| Tests + coverage floor | `pytest` |

## 6. Pure logic functions and state changes

- **Pure logic functions:** none added or changed. `check_service`, `get_consecutive_failures` and `get_services_status` get mechanical and annotation-only changes (RNF-2). Their runtime behavior is covered by the existing tests.
- **Database / schema:** no change and no new Alembic revision. The typed model declarations must render identical DDL (RNF-1b, already shown by the prototype).
- **Settings:** `SCHEDULER_INTERVAL_SECONDS` is removed. Nothing reads it (verified by grep), so this changes no behavior. Any `.env` that still sets it keeps working, because settings use `extra="ignore"`.
- **Scheduler lifecycle:** no change. `start_scheduler`, `stop_scheduler`, `schedule_service`, `unschedule_service` and the CRUD sync calls are untouched apart from import order and formatting.
- **Dependencies at runtime:** production image loses `pytest`/`pytest-cov`. Nothing in `app/` imports them.

## 7. Algorithms (pseudocode)

### 7.1 CI run
```
on pull_request(to master) or push(master):
  if event is pull_request and a run for the same PR is in progress: cancel it
  in parallel, independently, for job in [lint, format, typecheck, test]:
      checkout; setup python 3.11 (pip cache); pip install -r requirements-dev.txt
      run job.command
      report status "CI / <job>" = pass if exit code 0 else fail
  run result = fail if any job failed   # a failing job never stops the others
```

### 7.2 Coverage gate (inside `pytest`)
```
total = covered_statements(app/) / statements(app/) * 100   # TYPE_CHECKING blocks excluded
if round(total, 2) < 88: exit non-zero  ("Required test coverage of 88% not reached")
```

### 7.3 RNF-1 "no behavior change" verification (validation step, scratch only, never committed)
```
make a venv with requirements-dev.txt                       # same env for both snapshots
git worktree add <tmp>/before master
for tree in [<tmp>/before, working tree]:
    with DATABASE_URL=sqlite:// and cwd=tree:
        write json.dumps(app.openapi(), sort_keys=True, indent=2)  -> tree.openapi.json
        for table in Base.metadata.sorted_tables:
            emit CreateTable(table) compiled for postgresql dialect
            emit CreateIndex(i) for i in table.indexes sorted by name
            emit (column, python-side default, server default) for each column
        -> tree.ddl.sql
    pytest --collect-only -q                                -> tree.tests.txt
assert files byte-identical: openapi.json (RNF-1a), ddl.sql (RNF-1b), tests.txt (RNF-1c: same 50 node ids)
assert git diff master -- migrations/ is empty and no new revision file (RNF-1b)
review git diff master -- tests/: only mechanical and annotation-only hunks (RNF-1c)
review git diff master -- app/services app/workers app/db/session.py: mechanical and annotation-only (RNF-2)
git worktree remove <tmp>/before
```
The python-side default line is there because DDL alone cannot see client-side `default=200`/`default=60`/`default=True`. Without it, a dropped default in the model rewrite would go unnoticed.

## 8. Rendering / UI behavior

### 8.1 Pull request view
Each of the four jobs appears as its own check (`CI / lint` and so on), with a red ✗ or green ✓ and a link to its log. If one fails, the others still finish and show their own result. A shared-setup failure, such as a PyPI outage during `pip install`, shows as each job failing at its install step (allowed by RF-7).

### 8.2 `docs/architecture.md` outline (RF-12)
1. **Components**: API layer (`app/api/v1`), schemas, business logic (`app/services`), persistence (`app/db`), scheduler (`app/workers`), settings (`app/core/config.py`). One Mermaid component diagram (GitHub renders Mermaid).
2. **Lifecycle**: on startup the lifespan runs `start_scheduler()`, which uses its own `SessionLocal()` to load the active services and add one interval job each (`service_<id>`). On shutdown, `stop_scheduler()`. CRUD keeps jobs in sync: create schedules, update reschedules or unschedules depending on `is_active`, delete unschedules.
3. **One health check, step by step** (Mermaid sequence): an APScheduler thread calls `check_service_by_id(id)`, which opens its own `SessionLocal()`, loads the service (and skips it if missing or inactive), then `check_service`. That makes an httpx GET with `timeout=CHECK_TIMEOUT_SECONDS`. If the status code equals `expected_status` the check is UP, otherwise DOWN. Any exception means DOWN, with the error message truncated to 500 characters and no response time. Response time is in ms, rounded to 2 decimals. The `Metric` is added and committed; on error the session rolls back and logs; the session is always closed.
4. **Status derivation** (`GET /api/v1/status`):
   - Only active services are included. Services with no metrics yet are left out, and are not counted in `total_services`.
   - **DOWN** if the most recent `FAIL_THRESHOLD` metrics are all DOWN, otherwise **UP**.
   - **Average response time** covers all of the service's metrics. Null response times are ignored. Rounded to 2 decimals, and 0.0 if there is no data.
   - **24h uptime** is UP metrics divided by all metrics with `checked_at` in the last 24h (UTC), times 100, rounded to 2 decimals, and 0.0 if there are none.
5. **Settings table**: `DATABASE_URL`, `FAIL_THRESHOLD` (3), `CHECK_TIMEOUT_SECONDS` (10). **Known gap:** the default `check_interval` (60s) is fixed in the schema and model, not a setting.
6. **Known trade-offs**: tests on SQLite vs Postgres in production, and sync sessions in threads.

Every number and rule above is taken from the current code and must be re-checked against it during validation.

## 9. Technical decisions

| ID | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | **ruff** for lint and format | flake8 + isort + black | One pinned tool, so the formatter and linter can't conflict (RF-2). It's faster (RNF-3), and it's what the roadmap names. |
| D2 | **mypy** for type checking | pyright | mypy is named in the roadmap. Its pydantic plugin handles `BaseSettings()` and ORM-to-schema constructor calls (D5). |
| D3 | Line length **100** | 88 (default) / 120 | Smallest formatter diff that still keeps the multi-line query chains in `services/status.py` and `services/monitor.py` (RNF-2 areas). The diff was 48 app lines at 100, against 101 at 88. At 120, the chains collapse into one-liners that are hard to read. |
| D4 | Typed `DeclarativeBase` + `Mapped[...]` models | Single-line `# type: ignore` on every `Column[int]` use | Fixes the cause instead of adding ignores. The spec explicitly allows typed model declarations when RNF-1b holds, and the prototype showed byte-identical DDL. |
| D5 | Enable the `pydantic.mypy` plugin (default settings) | No plugin | Without it, `Settings()` fails ("missing DATABASE_URL"), and `MetricsPage(items=list[Metric])` fails because the ORM objects are validated at runtime with `from_attributes`. Fixing those would need casts or ignores in RNF-2 code. The plugin ships inside pydantic, so there is no new dependency. |
| D6 | `check_service(...) -> dict[str, Any]` | A new `TypedDict` | Clearly annotation-only in an RNF-2 area. A `TypedDict` would add a runtime class definition. |
| D7 | `response_model=None` plus `-> dict[str, str]` on `root()`/`health()` | Annotation alone / `-> Any` / leave the routes unannotated | Annotation alone changes the OpenAPI document (measured). Leaving them unannotated breaks strict mode. `response_model=None` keeps the document byte-identical (measured). These two routes are not in an RNF-2 area. |
| D8 | Separate `requirements-dev.txt` that includes `requirements.txt` | pip `[project.optional-dependencies]` in `pyproject.toml` / changing the Dockerfile | Keeps today's `pip install -r` workflow, and the Dockerfile stays untouched (RNF-5). |
| D9 | Coverage config in `pyproject.toml`; `pytest.ini` kept | Moving pytest config into `pyproject.toml` and deleting `pytest.ini` | Avoids deleting a file (ask-first rule) and avoids changing test discovery. |
| D10 | Four separate CI jobs | One job with four `if: always()` steps | Separate jobs give separate named statuses that can be marked required (RF-5, RF-7). The cost is about 20s of install per job, offset by caching, and still well under 5 minutes (RNF-3). |
| D11 | Behavior-equivalence check run ad hoc during validation (§7.3) | A committed script plus CI job | The spec asks for verification, not a new permanent tool. A committed script would itself need typing, linting and tests, which is scope creep. |
| D12 | Pin ruff 0.17.2 (not latest 0.17.4) | Latest release | Released at least 2 weeks ago. Fixed versions keep results reproducible (RNF-4). |

## 10. Test strategy

No new tests: the spec puts new tests out of scope. Verification per requirement:

| Req | Automated | Manual |
|---|---|---|
| RF-1 | `ruff check .` exits 0 locally and in CI | Inspect `select` covers the listed families (§4.1) |
| RF-2 | `ruff format --check .` exits 0; `ruff format . && ruff check .` exits 0 | |
| RF-3 | `mypy` exits 0 (strict, `app` + `tests`) | Grep that no global relaxation exists, and each `type: ignore` names a code and a reason |
| RF-4 | `pytest` exits 0 with "Required test coverage of 88% reached" | Temporarily set `fail_under = 99` locally and confirm `pytest` fails, then revert |
| RF-5, RF-7 | PR shows 4 named checks | Throwaway PR with a deliberate lint error: only `CI / lint` red, the other 3 complete. Close it unmerged and link it in the validation notes. |
| RF-6 | Run appears on `master` after merge | |
| RF-8 | | Push two commits quickly to a PR; the first run shows "cancelled" |
| RF-9 | | Workflow review: no `secrets.*`, `permissions: contents: read`, no `DATABASE_URL` |
| RF-10, RF-11 | `grep -rn SCHEDULER_INTERVAL_SECONDS` returns nothing outside `specs/` and `MEMORY.md` | Keys in `.env.example` equal the `Settings` fields plus `migrations/env.py` reads |
| RF-12 | | Read `docs/architecture.md` against the code (§8.2 checklist); README links it |
| RF-13 | | AGENTS.md and README commands match §5; no "not set up yet"; roadmap entry lists 4 checks |
| RNF-1 (a)(b)(c) | §7.3 procedure | §7.3 diff reviews |
| RNF-1 (d) | | `docker compose up --build`, then `curl /health` and `/api/v1/status` return 200. Needs Docker; it is not reachable from this WSL distro, so the owner runs it or enables WSL integration. |
| RNF-2 | | §7.3 diff review of the listed paths |
| RNF-3 | | Duration of each completed validation run shown on the PR is under 5 min |
| RNF-4 | | Versions in `requirements-dev.txt` use `==`; CI uses `python-version: "3.11"` |
| RNF-5 | | `docker compose build`, then `docker compose run --rm api pip show pytest` reports "not found" (Docker needed as in RNF-1d) |

## 11. Risks

| Risk | Mitigation |
|---|---|
| The coverage margin is thin: 88.09% → about 88.05% after removing one covered line. **One** new uncovered statement drops it to 87.77% and fails the gate. | Changes add no runtime statements except imports, which are covered. `TYPE_CHECKING` blocks are excluded. If it still drops, stop and record it under Open questions (spec edge case). |
| A typed model rewrite silently drops a client-side `default=` | §7.3 also compares per-column Python-side defaults, not only DDL |
| Upgrading pinned tools later changes results | Pins are exact. Bumping them is a separate change. |
| Strict mypy finds a real bug during implementation (none found in the probe) | Follow the spec edge cases: stop if it affects health data, otherwise a coded ignore plus a roadmap entry |

## 12. Approvals needed before implementation

1. **New dev dependencies:** `ruff==0.17.2`, `mypy==2.4.1` (AGENTS.md "ask first"; spec RNF-5).
2. **Moving `pytest`/`pytest-cov`** out of `requirements.txt`. This changes what the production image contains, though no Docker or compose file changes.
3. **New CI workflow** under `.github/workflows/`. It runs on GitHub; no deploy or secrets.

No schema or migration change, no Docker or compose edit, no file deletion.

## 13. RF coverage matrix

| Req | Covered by (plan section) |
|---|---|
| RF-1 | §3 (code), §4.1 (ruff lint), §5 |
| RF-2 | §3, §4.1 (ruff format, E501 ignored), §5 |
| RF-3 | §3 (models, routes, tests), §4.1 (mypy), D4, D5, D7 |
| RF-4 | §4.1 (coverage), §7.2 |
| RF-5 | §4.3, §7.1, D10 |
| RF-6 | §4.3 |
| RF-7 | §4.3 (independent jobs), §8.1 |
| RF-8 | §4.3 (concurrency) |
| RF-9 | §4.3 (permissions, no secrets, no `DATABASE_URL`) |
| RF-10 | §4.4 |
| RF-11 | §3 (`config.py`, `.env.example`, README), §6 |
| RF-12 | §8.2 |
| RF-13 | §3 (README, AGENTS.md, `docs/SPEC.md`), §5 |
| RNF-1 | §1 (prototype), §6, §7.3 |
| RNF-2 | §3 (per-file change type), §7.3 |
| RNF-3 | §4.3 (cache, timeouts), D10 |
| RNF-4 | §4.2, §4.3 |
| RNF-5 | §4.2, D8, §12 |
