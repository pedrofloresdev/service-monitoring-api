# 000 - Foundations

Status: draft

## Context and goal
The Service Monitoring API works (v1.0, 50 passing tests, ~88% coverage) but has no automated quality gate: no linting, no formatting standard, no type checking, and no continuous integration. Nothing stops a change that breaks tests, lowers coverage or adds untyped code from reaching the main branch. The example environment file is also out of date, which misleads anyone setting the project up.

Goal: set a verified quality baseline that every later spec builds on. Lint, format and strict type checks run clean on the whole codebase. Tests and coverage are enforced automatically on every change. The architecture is documented. None of this changes how the API behaves.

## Users
- **Owner/developer:** wants every change checked automatically, so regressions are caught before merging.
- **AI coding agent:** needs single, documented commands for lint, format, type check and tests, so it can verify its own work.
- **Portfolio reviewer:** expects visible CI status, a consistent code style and an accurate setup guide.

## User stories
- As the developer, I run one command for each check (lint, format, type check, tests) and get a clear pass or fail.
- As the developer, when I open a pull request, the checks run automatically and the PR cannot merge while any of them fails.
- As a new contributor, I copy the example environment file and it lists every setting the app reads, so I can start the app without guessing.
- As a reader of the repo, I can understand how the system is built from the documentation alone.

## Functional requirements
- RF-1: THE SYSTEM SHALL provide one documented lint command that reports zero violations on the whole codebase.
- RF-2: THE SYSTEM SHALL provide one documented formatting command, and a check mode of it that reports zero unformatted files on the whole codebase.
- RF-3: THE SYSTEM SHALL provide one documented type check command that runs in strict mode over all application code and reports zero errors.
- RF-4: THE SYSTEM SHALL provide one documented test command that runs the full suite and fails when total coverage of application code is below 85%.
- RF-5: WHEN a pull request is opened or updated, THE SYSTEM SHALL automatically run lint, format check, strict type check and tests with the coverage floor, and report a pass/fail result on the pull request.
- RF-6: WHEN a commit is pushed to the main branch, THE SYSTEM SHALL run the same checks as RF-5.
- RF-7: IF any check in RF-5 or RF-6 fails, THEN THE SYSTEM SHALL mark the run as failed and identify which check failed.
- RF-8: THE SYSTEM SHALL keep the automated checks able to run without access to production services or real secrets.
- RF-9: THE SYSTEM SHALL provide an example environment file that lists exactly the settings the application reads, each with a safe placeholder or its default value, and no unused settings.
- RF-10: THE SYSTEM SHALL include architecture documentation that describes the main components (API layer, business logic, persistence, background scheduler), how a health check flows from scheduling to stored metric, and how status is derived from metrics.
- RF-11: THE SYSTEM SHALL keep the project's agent and contributor documentation in sync with the real check commands, with none marked "not set up yet".

## Non-functional requirements
- RNF-1: No behavior change. All API routes, request/response shapes, status codes, status/uptime logic and the database schema stay identical. All 50 existing tests pass without changes to what they assert.
- RNF-2: A CI run on a clean checkout finishes in under 5 minutes.
- RNF-3: Check tools and versions are pinned, so local and CI runs give the same results.
- RNF-4: Development-only tools are declared separately from runtime dependencies, or at least documented as development-only. (Fully splitting the dependencies is out of scope.)

## Edge cases
- The type checker flags a real bug, not only a missing annotation: record it as a new roadmap spec and do not fix it here (RNF-1). Suppress it with an explicit, commented, narrowly scoped ignore that references the follow-up.
- A third-party library ships without type information: allow a narrowly scoped exception for that library only, never a global relaxation of strict mode.
- Automatic formatting rewrites many files: allowed, as long as RNF-1 holds and tests stay green.
- Coverage drops below 85% only because new tooling or config code is counted: exclude non-application code from the measurement instead of lowering the floor.
- A required check fails on the main branch after a merge: the run must be visibly red. Fixing it is the next priority.

## Out of scope
- Fixing bugs or changing behavior found while typing the code (they go to new specs).
- Removing the obsolete key from the container orchestration file.
- Fully splitting runtime and development dependencies.
- Correcting the README author link.
- Running tests against PostgreSQL (spec 003).
- Configuring branch protection on the hosting platform (the owner does this by hand; this spec only provides the required checks).
- Deployment or release automation.

## Completion criteria
- Lint, format check, strict type check and tests (coverage ≥ 85%) all pass locally with the documented commands.
- A pull request shows all CI checks passing. A deliberately broken change (for example a lint error) shows a failing check.
- The example environment file matches the settings the app actually reads.
- The architecture documentation exists and the agent/contributor docs list the real commands.
- The 50 original tests pass unmodified in what they assert. No API or schema difference against the main branch.
- MEMORY.md is updated with the new baseline.

## Open questions
- (none)
