# 000 - Foundations: tasks

Status: approved (2026-11-12)
Spec: `spec.md` (approved) · Plan: `plan.md` (approved)

Rules for every task:
- Implement one task, then stop.
- `pytest` must stay green, with coverage ≥ 88%, after each task.
- Mechanical and annotation-only, as the spec defines them, are the only change types allowed in RNF-2 areas.
- "Snapshot vs master" means the plan §7.3 procedure: a scratch script, run against a `master` worktree in the same venv, never committed.

## Tasks

- [x] **T1. Dev tooling and config.** Create `requirements-dev.txt` (`-r requirements.txt`, `ruff==0.17.2`, `mypy==2.4.1`, `pytest==8.3.5`, `pytest-cov==6.1.0`). Remove the `# Testing` block from `requirements.txt`. Create `pyproject.toml` with the ruff, mypy and coverage tables from plan §4.1. No source changes. RF-1, RF-2, RF-3, RF-4, RNF-4, RNF-5
  - Done when:
    - In a fresh Python 3.11 venv, `pip install -r requirements-dev.txt` succeeds.
    - `ruff --version` prints 0.17.2 and `mypy --version` prints 2.4.1.
    - `pytest` passes 50 tests and prints "Required test coverage of 88% reached".
    - Temporarily setting `fail_under = 99` makes `pytest` exit non-zero. The change is reverted afterwards.
    - `requirements.txt` no longer lists pytest or pytest-cov.

- [x] **T2. Mechanical pass.** Run `ruff format .` and `ruff check --fix .` over the checked code. This fixes import order, the 2 unused imports and the missing trailing newline. No other edits. RF-1, RF-2, RNF-2
  - Done when:
    - `ruff check .` exits 0.
    - `ruff format --check .` exits 0.
    - `pytest` is green with coverage ≥ 88%.
    - `git diff` contains only whitespace and wrapping, import order, the 2 removed imports and the trailing newline.
    - `git diff -- migrations/` is empty.

- [x] **T3. Typed persistence models.** In `app/db/base.py`, replace `declarative_base()` with `class Base(DeclarativeBase)`. Convert `Service` and `Metric` to `Mapped[...] = mapped_column(...)`, keeping every positional and keyword argument. Type the relationships, with cross-model imports under `TYPE_CHECKING`. RF-3, RNF-1b
  - Done when:
    - The snapshot vs master shows the Postgres DDL and the per-column Python-side and server defaults byte-identical.
    - No file is added under `migrations/versions/`.
    - `mypy` reports no errors in `app/db/`.
    - `pytest` is green with coverage ≥ 88%.

- [x] **T4. Annotate the rest of the application code.** Make these changes:
  - `main.py`: `lifespan -> AsyncIterator[None]`. `root()` and `health()` get `-> dict[str, str]` and `response_model=None`.
  - Route return annotations in `api/v1/*`.
  - `get_db -> Iterator[Session]`.
  - `check_service -> dict[str, Any]`.
  - `limit: int | None = None`, with the default unchanged.

  RF-3, RNF-1a, RNF-2
  - Done when:
    - `mypy app` exits 0.
    - The snapshot vs master shows the OpenAPI JSON (sorted keys) byte-identical.
    - The diff of `app/services`, `app/workers` and `app/db/session.py` contains only annotation-only changes.
    - `ruff check .` and `ruff format --check .` exit 0.
    - `pytest` is green with coverage ≥ 88%.

- [x] **T5. Annotate the test suite.** Make these changes:
  - Annotate fixtures, the helpers `make_metric` and `_fake_service`, and every test function (`-> None`).
  - `_fake_service` returns `cast(Service, SimpleNamespace(...))`.
  - No assertion, input or expected value changes.

  RF-3, RNF-1c
  - Done when:
    - `mypy` (configured files: `app` + `tests`) exits 0.
    - `pytest --collect-only -q` lists the same 50 node IDs as master.
    - The `tests/` diff contains only mechanical and annotation-only hunks.
    - `ruff check .` and `ruff format --check .` exit 0.
    - `pytest` is green with coverage ≥ 88%.

- [ ] **T6. Configuration hygiene.** Delete `SCHEDULER_INTERVAL_SECONDS` from `app/core/config.py` and its row from the README configuration table. Rewrite `.env.example` exactly as in plan §4.4. RF-10, RF-11
  - Done when:
    - `grep -rn SCHEDULER_INTERVAL_SECONDS .` matches only under `specs/` and `MEMORY.md`.
    - The keys in `.env.example` are exactly `DATABASE_URL`, `FAIL_THRESHOLD`, `CHECK_TIMEOUT_SECONDS`, which are the `Settings` fields plus what `migrations/env.py` reads.
    - The file contains no realistic credential.
    - `mypy`, `ruff check .` and `ruff format --check .` exit 0.
    - `pytest` is green with coverage ≥ 88%.

- [ ] **T7. CI workflow.** Create `.github/workflows/ci.yml` per plan §4.3: triggers, `permissions: contents: read`, concurrency with cancel for PRs only, and four independent jobs `lint`, `format`, `typecheck`, `test`. Each job uses Python 3.11 with pip cache and installs `requirements-dev.txt`. Actions are pinned to commit SHAs. No secrets and no `DATABASE_URL`. RF-5, RF-6, RF-7, RF-8, RF-9, RNF-3, RNF-4
  - Done when:
    - The file parses as YAML.
    - It contains no `secrets.` reference and no `DATABASE_URL`.
    - After pushing the branch and opening a PR to `master` (owner confirms the push first), the PR shows `CI / lint`, `CI / format`, `CI / typecheck` and `CI / test` all green.
    - The run takes under 5 minutes.

- [ ] **T8. Architecture document.** Create `docs/architecture.md` with the sections in plan §8.2, including the Mermaid component and sequence diagrams, the status rules, the settings table and the known gap about the fixed check interval. RF-12
  - Done when:
    - Each item in the §8.2 checklist is confirmed against the code, with the file and line noted while checking.
    - Both Mermaid diagrams render on GitHub in the PR file view.

- [ ] **T9. Documentation sync.** Make these changes:
  - README: add dev setup with `requirements-dev.txt`, a "Quality checks" section with the four commands from plan §5, the 4 CI check names to mark as required, a link to `docs/architecture.md`, and the container-only `PORT` note.
  - AGENTS.md `## Commands`: list the real commands and remove "not set up yet".
  - `docs/SPEC.md`: the roadmap entry lists lint, format, type check and tests.

  RF-12, RF-13
  - Done when:
    - Every command in AGENTS.md and the README runs green exactly as written.
    - `grep -n "not set up yet" AGENTS.md README.md` finds nothing.
    - Every setting named in the README exists in `Settings`.

- [ ] **T10. Validation evidence.** Run the full plan §7.3 equivalence check and record the results in `specs/000-foundations/validation.md`:
  - `docker compose up --build`, then `GET /health` and `GET /api/v1/status` return 200 (RNF-1d).
  - `docker compose run --rm api pip show pytest` reports not found (RNF-5).
  - A throwaway PR with a deliberate lint error shows only `CI / lint` red while the other 3 complete. The PR is closed unmerged and its link recorded (RF-7).
  - Two quick pushes to that PR show the first run cancelled (RF-8).
  - The durations of the completed runs are recorded (RNF-3).

  RF-5, RF-7, RF-8, RNF-1, RNF-2, RNF-3, RNF-5
  - Done when:
    - `validation.md` lists each completion criterion from the spec, with evidence: command output summaries and PR or run links.
    - Every item passes, or the failure is stated plainly.

## Coverage check

| Req | Tasks |
|---|---|
| RF-1 | T1, T2 |
| RF-2 | T1, T2 |
| RF-3 | T1, T3, T4, T5 |
| RF-4 | T1 |
| RF-5 | T7, T10 |
| RF-6 | T7 (verified on first push to `master` after merge) |
| RF-7 | T7, T10 |
| RF-8 | T7, T10 |
| RF-9 | T7 |
| RF-10 | T6 |
| RF-11 | T6 |
| RF-12 | T8, T9 |
| RF-13 | T9 |
| RNF-1 | T3 (b), T4 (a), T5 (c), T10 (all + d) |
| RNF-2 | T2, T4, T10 |
| RNF-3 | T7, T10 |
| RNF-4 | T1, T7 |
| RNF-5 | T1, T10 |
