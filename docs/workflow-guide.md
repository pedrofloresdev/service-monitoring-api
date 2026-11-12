# SDD workflow guide

How work gets done in this repository. Every change follows Spec-Driven Development (SDD):
the spec decides what gets built, and the code follows it. This guide is for the owner,
contributors and AI coding agents. The binding rules live in `docs/constitution.md` and
`AGENTS.md`; the file formats live in `.claude/skills/sdd/SKILL.md`. This guide explains how
they fit together in practice, with examples from specs 000 and 001.

## 1. Artifacts

| File | What it holds | Who changes it |
|---|---|---|
| `docs/constitution.md` | Non-negotiable principles (correct health data first, config-driven, tests first, ...) | Owner only, with explicit approval |
| `docs/SPEC.md` | Product vision, current scope and the numbered spec roadmap | Updated when a spec is added or finished |
| `specs/NNN-name/spec.md` | WHAT and WHY: users, EARS requirements (RF/RNF), edge cases, out of scope, completion criteria, open questions | Spec phase; later only through `/sdd-change` |
| `specs/NNN-name/plan.md` | HOW: files, config, algorithms, decisions with the discarded alternative, test strategy, RF coverage | Plan phase |
| `specs/NNN-name/tasks.md` | Small ordered tasks, each with a verifiable "Done when" | Tasks phase; checkboxes during implementation |
| `specs/NNN-name/validation.md` | Evidence per completion criterion: commands, outputs, PR and CI run links | During and after implementation |
| `MEMORY.md` | Current state, decisions with their reasons, lessons, next steps | End of every phase |
| `AGENTS.md` / `CLAUDE.md` | Stack, commands, conventions, limits for agents | When commands or conventions change |

A spec moves through `Status: draft` → `approved` → `implemented`.

## 2. The phases

Each phase ends with an explicit owner approval in the conversation. Nothing moves to the
next phase without it.

| Phase | Command | Output | Gate |
|---|---|---|---|
| Spec | `/sdd-spec NNN-name idea` | `spec.md` (draft) | Owner reads it |
| Clarify | `/sdd-clarify NNN-name` | A QA report from the read-only `reviewer` subagent: ambiguities, contradictions, uncovered edge cases, constitution conflicts | Repeat until no High findings remain, then the owner approves |
| Plan | `/sdd-plan NNN-name` | `plan.md` | Owner approves, including the sensitive items listed in its "Approvals needed" section |
| Tasks | `/sdd-tasks NNN-name` | `tasks.md` (≤ 10 tasks, otherwise split the spec) | Owner approves |
| Implement | `/sdd-implement NNN-name Tn` | Code, tests, one commit per task | Every check green after each task |
| Validate | `/sdd-validate NNN-name` | The `reviewer` re-runs every test and app check, requirement by requirement, and gives a verdict | "SPEC FULFILLED", then the owner merges |
| Change | `/sdd-change NNN-name requirement` | New or updated spec first, then plan, tasks and code | As for a new spec |
| Status | `/sdd-status NNN-name` | Current phase and next step | none |

### Spec and clarify

- Write requirements in EARS form (`WHEN ... THE SYSTEM SHALL ...`), and make every one verifiable. "Fast" or "robust" is not a requirement; "under 60 s on 1,000,000 rows" is.
- Put a **Definitions** section in when terms are ambiguous. In 000, "application code", "strict mode", "coverage floor", "mechanical change" and "annotation-only change" each settled an argument before it happened.
- The clarify loop is cheap and catches expensive mistakes. Spec 000 needed two passes: the second found that "annotation-only" made formatting impossible in the sensitive files.

### Plan: measure before deciding

Probe the real system before committing to a design, on scratch copies and throwaway
containers, never on project files:
- **000** measured the coverage baseline (88.09%, so the floor is 88) and prototyped typed models, proving the DDL and OpenAPI stayed byte-identical before planning on it.
- **001** found that Alembic's own comparison misses a `VARCHAR` length difference, which ruled it out for the drift check, and timed the migration SQL on 1,000,000 rows.

Each decision in the plan names the alternative it rejected and why.

### Implement: one task, test first, one commit

- Logic is test-first (constitution principle 4). Write the tests, watch them fail for the right reason, then make them pass. Spec 001's migration tests failed with "7 differences" and "no 'Deleted N' line" before the migration existed.
- After every task: `ruff check .`, `ruff format --check .`, `mypy`, `pytest` (coverage floor), plus the PostgreSQL checks when the task touches the database (section 4).
- Commit each task on its own with a conventional message ending in `Refs: specs/NNN-name Tn`, and tick its checkbox.
- If a task's "Done when" needs GitHub (CI runs, PRs), commit the code and leave the box unticked until the evidence exists.

### Validate

The reviewer does not trust `validation.md`. It re-runs the tests, the app and the CI
evidence itself, then reports PASS, FAIL, UNCOVERED or NOT YET VERIFIABLE per requirement.
Requirements that can only be checked after merge (for example 000 RF-6, CI on push to
`master`) are named explicitly, not assumed.

## 3. When implementation finds a problem

Do not quietly fix things outside the spec. Spec 000 is the reference case:

1. The manual compose check in 000 T10 found that every metric on PostgreSQL had `checked_at = NULL` (metrics endpoint 500, uptime always 0.0). The bug was older than 000.
2. Work stopped and the bug was recorded as **OQ-1** under the spec's Open questions, with evidence. Spec 000 forbade schema changes, so it could not be fixed there.
3. The owner chose a new spec. `/sdd-change` produced **001-migration-drift**, which went through clarify, plan, tasks, implement and validate like any other spec.
4. 001 was stacked on 000 and had to land before 000 was signed off. OQ-1 was then marked resolved in 000.

When a fix has no clean option (001 OQ-1: legacy rows with unknown timestamps could be
deleted, fabricated, or kept nullable), the spec lists the options and the owner decides. A
departure from a constitution principle is recorded as an explicit, owner-approved exception.

## 4. Quality gates

| Check | Command | In CI as |
|---|---|---|
| Lint | `ruff check .` | `lint` |
| Format | `ruff format --check .` | `format` |
| Strict types | `mypy` | `typecheck` |
| Tests + coverage floor | `pytest` | `test` |
| Migration history vs models, PostgreSQL tests | `python -m tools.schema_check` and `pytest -m postgres --no-cov` (need `docker compose up -d db` and `TEST_POSTGRES_URL`) | `migrations` |

Setup: `pip install -r requirements-dev.txt` (Python 3.11). Details are in the README's
"Quality Checks" section.

Manual checks still matter. The default suite builds tables from the models on SQLite, so
only a real `docker compose up --build` and an end-to-end test of the write path (register a
service, let the scheduler store checks, read the metrics and status) found OQ-1. Run that
smoke test before validating any spec that touches persistence or the scheduler.

## 5. Branches, pull requests and CI evidence

- One branch per spec: `feature/NNN-name`. Never commit to `master`.
- CI runs on pull requests to `master` and on pushes to `master`.
- **Stacked specs:** when one spec must land before another is signed off, branch it from the first, merge it into the first branch with its own PR, and let the first branch's PR to `master` carry both (000 PR #1 carried 001 through PR #2).
- **Negative CI evidence:** to prove a check fails when it should, use a throwaway draft PR to `master` titled "DO NOT MERGE", push the deliberate failures, record the run IDs in `validation.md`, then close it unmerged and delete the branch. PR #3 proved lint-only failure, run cancellation and migrations-only failure on drift.
- Run all local checks before every push, throwaway commits included. One validation run was spoiled by an over-long comment that tripped `format`.

## 6. Writing `validation.md`

One table row per completion criterion and per non-functional requirement:
**criterion | PASS / FAIL / PENDING | evidence**. Evidence is concrete:
- the exact command and the key line of its output,
- PR numbers, CI run IDs and commit SHAs,
- for manual checks, what was done and what was observed (for example "registered a service, after 23 s the metrics endpoint returned 200 with `checked_at` 20:35:35Z and 20:35:45Z").

State failures and your own mistakes plainly, and say how they were resolved.

## 7. Lessons so far

- A test suite that builds tables from the models cannot see migration drift; verify the migrated schema on the real database engine.
- If a spec restricts sensitive code to "annotation-only" changes, it must also allow mechanical formatter and import changes, or it cannot be satisfied.
- FastAPI turns a route's return annotation into a response model; annotating routes without `response_model` silently changes OpenAPI.
- Coverage `fail_under` compares the rounded total: set `precision = 2`, or 87.6% passes a floor of 88.
- Measure with probes before planning; three of the plan decisions in 000 and 001 came straight from measurements.
