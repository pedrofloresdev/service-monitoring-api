# 000 - Foundations

Status: approved

## Context and goal
The Service Monitoring API works (v1.0, 50 passing tests, ~88% coverage) but has no automated quality gate: no linting, no formatting standard, no type checking, and no continuous integration. Nothing stops a change that breaks tests, lowers coverage or adds untyped code from reaching the default branch. The example environment file is also out of date: it lists settings the app never reads and misses settings it does read.

Goal: set a verified quality baseline that every later spec builds on. Lint, format and strict type checks run clean on the checked code. Tests and a coverage floor are enforced automatically on every change. The architecture is documented. None of this changes how the API behaves.

## Users
- **Owner/developer:** wants every change checked automatically, so regressions are caught before merging.
- **AI coding agent:** needs single, documented commands for lint, format, type check and tests, so it can verify its own work.
- **Portfolio reviewer:** expects visible CI status, a consistent code style and an accurate setup guide.

## User stories
- As the developer, I run one command for each check (lint, format, type check, tests) and get a clear pass or fail.
- As the developer, when I open a pull request, the checks run automatically and the pull request shows a separate pass/fail status for each check, which I can mark as required on the hosting platform.
- As a new contributor, I copy the example environment file and it lists every setting the app reads, so I can start the app without guessing.
- As a reader of the repo, I can understand how the system is built from the documentation alone.

## Definitions
- **Application code:** the production source package (API layer, business logic, persistence, configuration, scheduler).
- **Test suite:** the automated tests and their shared fixtures.
- **Migration history:** the database revision scripts. This spec never edits them.
- **Checked code:** application code plus test suite. Migration history is excluded from lint, format and type checks (owner-approved exception to constitution principle 8, see Out of scope).
- **Strict mode:** the type checker's strictest built-in preset, enabled for all checked code, with no global relaxation. The only allowed exceptions are those in Edge cases.
- **Coverage:** line coverage of application code only. The test suite and migration history are not measured.
- **Coverage floor:** the integer part of the line coverage of application code, measured on the default branch before any change from this spec (for example 88.4% -> 88%). Recorded in the plan.
- **Default branch:** `master`.
- **Mechanical change:** a change produced by or required by the formatter or linter that does not alter runtime behavior: whitespace and line wrapping, quote style, import ordering, removing unused imports or unused local variables, and suppression comments allowed by Edge cases. Allowed in all checked code.
- **Annotation-only change:** adds or corrects type annotations; imports used only for typing (including blocks evaluated only by the type checker); typed declaration syntax for persistence models; `cast(...)` calls; and type-ignore comments allowed by Edge cases. It does NOT include: changing control flow, default values (including framework dependency-injection defaults), exception handling, call arguments, or how values are created, compared or stored; and it does NOT include adding runtime assertions.
- **Real bug:** a type-checker finding where the code, for some input the API accepts or some state the database can hold, would raise an unhandled error or produce a wrong value at runtime. A finding caused only by a typing limitation (for example untyped persistence model attributes, or a library's incomplete types) is not a real bug.

## Functional requirements
- RF-1: THE SYSTEM SHALL provide one documented lint command that reports zero violations on the checked code, using a rule set declared in the project configuration that includes at least these rule families: syntax errors and undefined names; unused imports and variables; import ordering; mutable default arguments; bare `except` clauses; and comparisons to `None` with `==`/`!=`.
- RF-2: THE SYSTEM SHALL provide one documented formatting command, and a check mode of it that reports zero unformatted files on the checked code. Running the formatter and then the linter SHALL produce zero lint violations.
- RF-3: THE SYSTEM SHALL provide one documented type check command that runs in strict mode over the checked code and reports zero errors.
- RF-4: THE SYSTEM SHALL provide one documented test command that runs the full test suite and fails when coverage is below the coverage floor.
- RF-5: WHEN a pull request targeting the default branch is opened, reopened or updated (including drafts and pull requests from forks), THE SYSTEM SHALL automatically run lint, format check, strict type check and tests with the coverage floor, and report a separate, named pass/fail status for each check on the pull request.
- RF-6: WHEN a commit is pushed to the default branch, THE SYSTEM SHALL run the same checks as RF-5.
- RF-7: IF any check in RF-5 or RF-6 fails, THEN THE SYSTEM SHALL mark the run as failed and show which check failed by its named status. A failing check SHALL NOT prevent the other checks from running to completion. (If a shared setup step such as dependency installation fails, every check may show as failed.)
- RF-8: WHEN a newer commit is pushed to the same pull request, THE SYSTEM SHALL cancel the in-progress run for the previous commit.
- RF-9: THE SYSTEM SHALL configure the automated checks with no secrets. Network use is limited to checking out the code, setting up the Python toolchain and CI tooling, and installing declared dependencies. The test run SHALL use the test suite's own in-memory database setting (CI does not override it). Any other step that imports the application SHALL receive a non-secret dummy database setting from the CI configuration.
- RF-10: THE SYSTEM SHALL provide an example environment file that lists exactly the settings the application code and the migration runner read, each with its default value or an obvious placeholder (for example `change-me`), and no other keys. It SHALL note that the container setup overrides the database setting.
- RF-11: THE SYSTEM SHALL remove the declared but never-read scheduler interval setting from the configuration, the example environment file and the documentation.
- RF-12: THE SYSTEM SHALL include an architecture document in the docs area, linked from the README, that contains: (a) the main components (API layer, business logic, persistence, background scheduler) and their responsibilities; (b) the flow of one health check from scheduling to stored metric, including session handling and timeout; (c) the rules that derive UP/DOWN, average response time and 24h uptime from metrics, with the settings that control them. Every setting, rule and component it names SHALL match the code, including the known gap that the default check interval is a fixed value, not a setting.
- RF-13: THE SYSTEM SHALL update the agent instructions and the README so that they list the real lint, format, type check and test commands, none is marked "not set up yet", and every setting they describe matches the code. The README SHALL also document container-only variables (such as the server port) that are not part of the example environment file, and list the CI check names to mark as required. The product roadmap entry for this spec SHALL list all four checks (lint, format, type check, tests).

## Non-functional requirements
- RNF-1: No behavior change. Verified by all of:
  - (a) The API schema document (OpenAPI), generated by the application's own schema function and serialized as JSON with sorted keys, is byte-identical before and after the change, with both snapshots taken in the same environment with the same installed dependencies.
  - (b) The database DDL rendered from the persistence model definitions for the PostgreSQL dialect is identical before and after the change, and no new migration is added.
  - (c) The 50 existing tests pass. Edits to test files are limited to mechanical and annotation-only changes. No assertion, test input or expected value changes, and no test is removed, renamed or split.
  - (d) Manual check: the containerized stack starts from a clean build (which applies the migration history to PostgreSQL), and `GET /health` and `GET /api/v1/status` respond successfully.
- RNF-2: In the scheduler, database session handling, check execution, timestamp handling and status/uptime logic, only mechanical and annotation-only changes are allowed.
- RNF-3: A CI run on a clean checkout finishes in under 5 minutes wall-clock for the whole workflow, on every completed (not cancelled) run used to validate this spec.
- RNF-4: Check tool versions are pinned exactly, and CI uses the same Python minor version as the runtime (3.11) and the same dependency declarations as local runs. Differences caused only by unlocked transitive runtime dependencies are accepted (see Out of scope).
- RNF-5: Development-only tools, including the existing test runner and coverage plugin, are declared in a separate development dependency list and are not installed in the production container image. The container build configuration is not changed. Adding any new development tool needs owner approval in the plan phase.

## Edge cases
- The type checker flags a real bug that could affect health data (status, uptime, response time, or metric storage): stop work and record it under Open questions. It is not fixed in this spec (RNF-1, RNF-2). The owner decides through a spec change: either a new spec fixes it first, or it is accepted as a known gap and suppressed as below.
- The type checker flags a real bug that does not affect health data: do not fix it. Suppress it with a single-line type-ignore comment that names the error code and references a new roadmap entry.
- A finding caused only by a typing limitation: fix it with annotation-only changes where possible (including typed persistence model declarations, allowed only if RNF-1b holds). Otherwise suppress it with a single-line type-ignore comment that names the error code and explains the limitation.
- A third-party library ships without type information: allow a per-library exception in the type checker configuration, or a pinned type-stub development dependency (owner approval required). Never a global relaxation of strict mode.
- A route has no explicit response model and adding a return annotation would change the API schema document: keep the document identical (RNF-1a).
- A parameter with a `None` default but a non-optional type: correct the annotation only. The default value stays.
- The linter flags the web framework's dependency-injection or parameter defaults (for example as calls in default arguments): declare those framework calls as allowed in the lint configuration. Do not rewrite the signatures.
- Any other lint finding that cannot be fixed with a mechanical change: suppress it with a single-line suppression comment that names the rule code and gives a reason. Disabling a rule for the whole project or a whole file is allowed only for the framework case above.
- Automatic formatting rewrites many files, including those covered by RNF-2: allowed, since formatting is a mechanical change.
- Coverage drops below the floor: lines evaluated only by the type checker may be marked as excluded from coverage. For any other cause, stop and record it under Open questions. Do not lower the floor, exclude other application code, or add tests in this spec.
- A required check fails on the default branch after a merge: the run is visibly red, with the failing check named.
- A pull request comes from a fork: checks run the same way, since they need no secrets (RF-9).

## Out of scope
- Fixing bugs or changing behavior found while typing the code (they go to new specs, see Edge cases).
- Adding new tests or raising the coverage floor.
- Type-checking, linting or formatting the migration history (owner-approved exception to constitution principle 8).
- Making the default check interval configurable. It stays a fixed value in the code (known gap against constitution principle 3, recorded in project memory as a roadmap candidate).
- The hardcoded database credentials in the container orchestration file (known gap against constitution principle 7, recorded in project memory; not an approved exception).
- Removing the obsolete key from the container orchestration file.
- Locking transitive runtime dependencies.
- Correcting the README author link.
- Running tests or migrations against PostgreSQL in CI (roadmap spec 003).
- Configuring branch protection on the hosting platform. The owner marks the checks as required by hand.
- Deployment or release automation.

## Completion criteria
- Lint, format check, strict type check and tests (coverage ≥ floor) all pass locally with the documented commands.
- A pull request to the default branch shows each check as a separate passing status, and the run takes under 5 minutes.
- A deliberately broken change (a lint error) on a throwaway pull request shows only the lint status failing while the others complete. The pull request is closed unmerged and linked in the validation notes.
- The example environment file matches exactly the settings the app and migration runner read. The unused scheduler interval setting is gone.
- The production container image does not contain the development tools.
- The architecture document exists, is linked from the README and matches the code. The agent instructions, README and roadmap entry are updated per RF-13.
- RNF-1 (a)-(d) all verified and stated in the validation notes.

## Open questions
- **OQ-1 (blocking, real bug affecting health data; found 2026-11-12 during T10 manual check, pre-existing on `master`).** On PostgreSQL migrated with the existing revisions, `metrics.checked_at` has no server default and is nullable, while the ORM model declares `server_default=now()` and `nullable=False`. The app inserts metrics without `checked_at`, so every stored metric has `checked_at = NULL`. Effects observed on a clean `docker compose up --build`: `GET /api/v1/metrics/services/{id}` returns 500 (response validation of `checked_at`), `uptime_last_24h` is always 0.0, and newest-first ordering is undefined. The test suite does not catch it because it builds tables from the models (SQLite `create_all`), not from the migrations. Related drift between migrations and models: `metrics.status` is nullable `VARCHAR` (model: `String(4)`, not null) and `services.expected_status`, `check_interval`, `is_active` are nullable (model: not null). Fixing it needs a new Alembic revision, which this spec forbids (RNF-1b). Not fixed here. Owner to decide via a spec change.
