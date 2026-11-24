# 003c - Demo mode: periodic reset and notice

Status: draft (revised 2026-11-24 after `/sdd-clarify` pass 3; the single-process lock moved to 003d)

## Context and goal
Spec 003a adds demo mode: seeded services, protection and caps on visitor services. Nothing returns the demo to a known state, so after a few days it shows whatever visitors left behind.

Goal: while demo mode is on, the API removes everything visitors created, at fixed times in UTC. It catches up whenever a reset was missed, retries a failed one, and tells visitors when the next reset happens.

This spec depends on 003a and 003d (single process). 003b depends on it. Implementation order: 003a, then 003d, then 003c, then 003b. All four are needed before a public deployment.

## Users
- **Visitor:** finds the demo in a known state, and can see from `GET /` that it resets, and when.
- **Owner/operator:** doesn't clean up after visitors, even after downtime or a failed reset.

## User stories
- As a visitor, I can see from `GET /` that the demo resets, and when.
- As the owner, the demo removes visitor data every day at a fixed UTC time, catching up after downtime and retrying after a failure.
- As a visitor writing during a reset, I wait briefly or get a clear "try again shortly" answer, never a half-applied change.

## Definitions
- **Write request:** as defined in 003b: a request with method POST, PUT, PATCH or DELETE, on any path.
- **Reset slots:** every instant `A + k × I` in UTC, for whole numbers `k`, where `A` is the reset anchor time of day on 1970-01-01 and `I` is the reset interval.
  - With the defaults (00:00 UTC, 24 hours), there is a slot every day at 00:00 UTC.
  - With an interval of 168 hours, slots fall on Thursdays, because 1970-01-01 was a Thursday.
- **Current slot:** the latest slot at or before now. **Next slot:** the earliest slot strictly after now.
- **Last completed slot:** the slot that the most recent completed reset served. It is stored, and is empty on a database where no reset has completed.
- **Due reset:** a reset is due when the last completed slot is empty or earlier than the current slot.

### Settings
Read once at startup, validated only while demo mode is on (003a RF-2).

| Setting (environment variable) | Default | Valid values |
|---|---|---|
| Reset interval (`DEMO_RESET_INTERVAL_HOURS`) | 24 | integer 1 to 168 |
| Reset anchor time (`DEMO_RESET_ANCHOR`) | `00:00` | UTC time of day, exactly `HH:MM`, `00`–`23` and `00`–`59` |
| Due-reset poll interval (`DEMO_RESET_POLL_SECONDS`) | 60 | integer 10 to 3,600 |
| Reset retry delay (`DEMO_RESET_RETRY_MINUTES`) | 10 | integer 1 to 1,440 |
| Reset maximum duration (`DEMO_RESET_MAX_SECONDS`) | 30 | integer 1 to 600 |
| Write wait during a reset (`DEMO_RESET_WRITE_WAIT_SECONDS`) | 5 | integer 0 to 60 |

## Functional requirements

### Settings and startup
- RF-1: WHILE demo mode is on, IF any 003c setting is unparsable or outside its valid values, THEN THE SYSTEM SHALL refuse to start with an error naming the environment variable.
- RF-2: WHILE demo mode is on, WHEN the system starts, THE SYSTEM SHALL proceed in this order, and SHALL start serving requests only after step 6:
  1. validate settings and the start command (003a RF-1 to RF-4, 003b RF-1 and RF-3, 003d RF-1 to RF-3, this spec's RF-1);
  2. take the demo lock (003d RF-4);
  3. seed (003a RF-5, RF-6);
  4. run a due reset if there is one (RF-6);
  5. start the scheduler, which schedules every active service and checks seeded services at once (003a RF-8);
  6. start the due-reset poll (RF-6).

  A failure in steps 1 to 3 refuses startup. A failed reset in step 4 is handled as in RF-9, and startup continues.
- RF-3: The worst-case time from start to serving is about: the lock wait timeout + one resolution timeout (seed validation) + the reset maximum duration. THE SYSTEM SHALL document it, and the container's healthcheck start period SHALL be at least that long with the default settings.

### Reset
- RF-4: WHEN a reset runs, THE SYSTEM SHALL:
  - stop admitting new write requests (RF-7);
  - wait for write requests already being processed to finish;
  - then, in one database transaction, delete every visitor service and its stored checks, apply 003a RF-5 to the seeded services, and store the current slot as the last completed slot.

  The data changes and the slot are committed together or not at all. Seeded and ordinary services and their stored checks are kept.
- RF-5: WHEN a reset commits, THE SYSTEM SHALL bring the schedules in line with the database (every active service has exactly one schedule, and no schedule exists for a deleted or inactive service), then notify 003b so it clears its per-client attribution, then admit writes again. The reset ends only after these steps. A reset that does not commit doesn't notify 003b, and also ends by reconciling the schedules and admitting writes.
- RF-6: WHILE demo mode is on, THE SYSTEM SHALL check whether a reset is due:
  - at startup (RF-2 step 4);
  - at each slot;
  - every due-reset poll interval.

  WHEN one is due and no reset is running or waiting to retry, THE SYSTEM SHALL start it. At most one reset runs at a time. Several missed slots lead to a single reset, because a reset always serves the current slot. A slot missed while running (a paused machine, a clock jump forward, a late scheduler) is caught up by the poll, at most one poll interval late.
- RF-7: WHILE a reset is running, THE SYSTEM SHALL keep serving non-write requests normally. A write request that arrives during a reset SHALL:
  - be checked for rate limits first (003b), so a 429 is never held;
  - then wait up to the write wait setting for the reset to end;
  - IF the reset ends within the wait, THEN be processed normally, with every check (404, 403, caps, duplicates) evaluated after the reset;
  - IF the reset doesn't end within the wait, THEN be answered `503` with `Retry-After: 5` and `{"detail": "Demo reset in progress. Retry in a few seconds."}`, and not be processed.
- RF-8: THE SYSTEM SHALL bound each reset's database work by the reset maximum duration, enforced by the database for that transaction. IF the limit is exceeded, THEN the database rolls the transaction back, and the reset is a failed reset (RF-9). The system SHALL not interrupt a reset in any other way.
- RF-9: IF a reset fails (an error, or RF-8's limit), THEN THE SYSTEM SHALL:
  - log the failure;
  - leave the database as it was before that reset;
  - end the reset as in RF-5 (reconcile the schedules, admit writes, no 003b notification);
  - keep serving and checking;
  - retry after the reset retry delay, and keep retrying at that delay while the reset is still due.
- RF-10: WHEN a check finishes for a service that no longer exists, deleted by a reset or by a delete request, THE SYSTEM SHALL store nothing for it, and SHALL log it at warning level, not as an error. This applies whether demo mode is on or off (RNF-1). On PostgreSQL, service ids are never reused, so a late result can never attach to another service.
- RF-11: THE SYSTEM SHALL compute slots and due resets only from the application's UTC clock, using time-zone-aware values. The database clock is not used. IF the stored last completed slot is later than now (the clock moved back), THEN THE SYSTEM SHALL log a warning and replace it with the current slot without running a reset, so normal scheduling resumes at the next slot.

### Demo notice
- RF-12: WHILE demo mode is on, THE SYSTEM SHALL add two fields to the API info response (`GET /`) for every request:
  - `demo_notice`: `Demo data resets every N hours.`, where N is the reset interval (`Demo data resets every 1 hour.` when N is 1);
  - `next_reset_at`: in UTC, ISO 8601 with seconds precision ending in `Z`, rounded up to the next whole second:
    - while no reset is due: the next slot;
    - while a reset is running: the current time;
    - while a retry is pending: the earlier of the retry time and the next slot.

  The existing fields don't change. While demo mode is off, neither field is present. `GET /` keeps no response schema in the API schema document, as decided in spec 000, so the document stays identical.

### Switching off and documentation
- RF-13: WHILE demo mode is off, THE SYSTEM SHALL not reset, poll or show the notice. The stored last completed slot is kept.
- RF-14: THE SYSTEM SHALL document:
  - the settings, the slot formula (including the Thursday consequence of 168 hours), catch-up, the poll, retry, the maximum duration and the write wait with its 503;
  - the notice;
  - the worst-case startup time (RF-3);
  - that a reset runs at the first start and when demo mode is turned on after a gap (Edge cases);
  - the owner-approved exception to constitution principle 1.

  The detailed reference goes in the architecture document. The product SPEC and README summarize it and link there. In addition:
  - `docs/constitution.md` gains an "Approved exceptions" list that points to the exceptions in specs 001 and 003c. No principle text changes. The owner approved this on 2026-11-24.
  - AGENTS.md notes that `GET /` is the one response without a schema, by decision of spec 000.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change, except RF-10:
  - a check for a deleted service is logged as a warning instead of an error;
  - on SQLite, its result is no longer stored as an orphan.

  All existing tests pass with unchanged outcomes, and the API schema document is identical.
- RNF-2: The last completed slot is the only stored schema change in this spec. It is made through a versioned migration with owner approval in the plan (constitution principle 5). The migration is additive, so it is safe to apply while an older process still serves. The spec 001 migration check stays green.
- RNF-3: **Benchmark** (measured and recorded in the validation notes; not a CI gate): with default settings, a reset at maximum size completes in under 2 seconds on the containerized PostgreSQL. Maximum size is 20 visitor services, each with one day of checks at 300 s, so 288 checks each, plus the seeded services and their history.
- RNF-4: Time-based behavior (slots, due resets, the poll, retry, the write wait, `next_reset_at`) is tested with an injectable clock, never by waiting.
- RNF-5: Behavior that depends on the database engine is verified on PostgreSQL by `postgres`-marked tests:
  - reset atomicity and the slot in the same commit;
  - the maximum-duration rollback;
  - RF-10 with a check in flight;
  - time-zone-aware slot storage.
- RNF-6: Schedule requirements (RF-5, RF-9) are verified with a real scheduler instance, not a mock.
- RNF-7: The spec 000 to 003a and 003d quality gates stay green.
- RNF-8: Automated tests cover, at minimum:
  - RF-1, and the RF-2 order, including seeding before the catch-up reset (history kept for an adopted visitor service);
  - reset contents and atomicity;
  - waiting for in-flight writes;
  - schedule consistency after success and failure;
  - the 003b notification only after a commit;
  - due-reset detection at the first start, after downtime, at a slot, by the poll after a missed slot, and as a single reset for several missed slots;
  - retry after a failure, and the RF-8 rollback;
  - a write during a reset that completes within the wait, one that gets a 503, and a 429 not held;
  - RF-10 after a reset and after a delete;
  - RF-11 with the clock moving back;
  - RF-12 in each state, and absent with demo mode off;
  - demo mode off changing nothing except RNF-1's exceptions.

## Owner-approved exception to constitution principle 1
Owner approval, 2026-11-23: "record the reset's bulk deletion of visitor services and their checks as an owner-approved exception, in the same format as spec 001's. Seeded history is never deleted or fabricated."

This is a standing exception to constitution principle 1 ("never ... drop ... a metric"), limited to demo mode. A reset deletes every visitor service together with all of its stored checks (RF-4). Seeded services' checks are never deleted, altered or fabricated (003a RF-9). Ordinary services' checks are never touched by a reset. Outside demo mode, the exception does not apply.

Not storing a check whose service was already deleted (RF-10) is not part of this exception. The check belongs to no existing service, and PostgreSQL already refuses it today. `docs/constitution.md` lists this exception (RF-14), as approved by the owner on 2026-11-24.

## Edge cases
- The first start of demo mode on a database: the last completed slot is empty, so a reset runs at once. A fresh database has no visitor data, so nothing is lost.
- Demo mode is turned on again after a gap: the last completed slot is older than the current slot, so a reset runs at startup and removes visitor services left from before (they keep origin "visitor", per 003a).
- The process is stopped across one or more slots: one catch-up reset runs at the next startup, after seeding.
- A slot passes while the machine is paused: the poll catches it at most one poll interval after it resumes.
- A process in a crash loop: each start runs a reset only if one is due. Once a reset completes, restarts don't repeat it until the next slot.
- A reset fails because the database is briefly unavailable: it retries every reset retry delay while still due. If the database is down at a restart, startup is refused (003d, 003a) and the platform restarts the process.
- A reset is running when a slot or poll arrives: ignored, because at most one reset runs. The next check after it ends decides whether another is due.
- Check jobs inserting results while a reset deletes their service: they wait for the reset's transaction, which RF-8 bounds, then store nothing (RF-10).
- A read request that overlaps a reset may see some data from before and some from after. Only that request is affected. Accepted.
- A visitor creates a service a moment before a reset: the reset waits for that write to finish, then deletes the service.

## Out of scope
- The single-process lock (003d), rate limits (003b), seeding and caps (003a).
- Configurable reset contents.
- Notifying visitors other than through `GET /`.
- Hosting configuration for any specific platform.

## Completion criteria
- Resets run at the configured UTC slots, catch up after downtime and missed slots, retry after failure, roll back past the maximum duration, and leave exactly one schedule per active service.
- Visitor data is removed. Seeded and ordinary services keep their checks.
- Writes during a reset either complete after it or get the 503 within the write wait.
- `GET /` shows the notice and the correct `next_reset_at` in each state in demo mode, and is unchanged with demo mode off.
- The RNF-3 benchmark is measured and recorded. The docs, the constitution's exceptions list and the AGENTS.md note are updated per RF-14. The quality gates are green in CI.

## Decisions made in revision
Pass 2 decisions F1 to F16 still apply unless refined here or moved to 003d. Items marked "owner" came from your answers.

- **J1 (owner, decision 1) – split:** the lock moved to 003d. This spec keeps the reset, catch-up, retry and the notice.
- **J2 (owner, decision 3) – a reset that runs too long:**
  - the "abort a running reset" rule is dropped, because a thread can't be killed safely (finding 1.2);
  - the database rolls back a reset that exceeds its maximum duration (RF-8);
  - writes wait at most the write wait setting (default 5 s), then get a 503 (RF-7);
  - waiting writes therefore tie up request threads for seconds, not 30 s (findings 2.4 and 4.3).
- **J3 (owner, decision 4) – constitution:** `docs/constitution.md` gets an "Approved exceptions" list (RF-14). The approval is quoted in the exception section (findings 2.10 and 4.1).
- **J4 (owner, decision 5) – first start and demo-off gaps:** both run a reset, stated explicitly (RF-6, Edge cases; finding 1.3).
- **J5 (2.2) – the scheduler skips late jobs:** APScheduler's default misfire grace is 1 s, so a late reset job is skipped. A due-reset poll (a setting, default 60 s) catches up any missed slot (RF-6).
- **J6 (2.1, 3.5) – slot-based catch-up:** the stored value is the slot a reset served, not its wall-clock completion time. A backward clock jump replaces it with the current slot (RF-11), so resets are never suppressed for days. Only the application clock is used.
- **J7 (1.5, 1.6, 2.3, 3.4) – the write gate:**
  - every write, by method as in 003b, is gated after the 429 check;
  - in-flight writes finish before the reset starts;
  - the reset ends only after reconciling schedules and notifying 003b;
  - all later checks run after the reset.
- **J8 (1.4) – `next_reset_at`:** defined for each state and rounded up to whole seconds.
- **J9 (2.7, 2.8) – late checks:** RF-10 applies with demo mode on and off. RNF-1 lists its exact demo-off changes, and the principle 1 exception now covers only reset deletions.
- **J10 (2.5) – startup time:** stated (RF-3). The container's healthcheck start period must cover it, a Docker change that needs approval in the plan.
- **J11 (2.9) – implementation wording:** "tied to the connection" moved to 003d as behavior, and "foreign keys" was replaced by behavior (RF-10).
- **J12 (2.12) – `GET /` schema:** the exception is recorded in AGENTS.md (RF-14).
- **J13 (3.6) – concurrent triggers:** at most one reset at a time. Triggers during a reset are ignored, and the due check afterwards decides.
- **J14 (3.10) – benchmark:** its size is fixed at the default settings.
- **J15 (3.9, 1.8 of 003a) – 168-hour interval:** the Thursday consequence is documented.
- **J16 (4.2) – tunables:** the poll interval and write wait are settings. The `Retry-After: 5` on the 503 is a fixed protocol hint, not a tunable.

## Open questions
- (none)
