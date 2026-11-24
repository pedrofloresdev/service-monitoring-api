# 003c - Demo mode: single process, periodic reset and notice

Status: draft (created 2026-11-24 from the original 003 after `/sdd-clarify` pass 2)

## Context and goal
Spec 003a adds demo mode: seeded services, protection and caps on visitor services. Two problems remain for a public demo:
- Nothing returns the demo to a known state. After a few days it shows whatever visitors left behind.
- Demo mode keeps state in a single process (this spec's schedule, and spec 003b's in-memory limits). Two processes against the same database would double the schedule and split the limits.

Goal: while demo mode is on, the API:
- runs as a single process, which it enforces;
- removes everything visitors created at fixed times in UTC, catching up whenever a reset was missed;
- tells visitors when the next reset happens.

This spec depends on 003a and is needed, with 003b, before a public deployment. Implementation order: 003a, then 003c, then 003b.

## Users
- **Visitor:** finds the demo in a known state, and can see from `GET /` that it resets, and when.
- **Owner/operator:** doesn't clean up after visitors, and deploys without two processes ever running demo mode at once.

## User stories
- As a visitor, I can see from `GET /` that the demo resets, and when.
- As the owner, the demo removes visitor data every day at a fixed UTC time, even after downtime or a failed reset.
- As the owner, when I deploy a new version, the new process takes over safely from the old one or refuses with a clear error.

## Definitions
- **Demo process lock:** an exclusive claim, held by the process that runs demo mode against a database, for as long as it runs.
- **Reset times:** every instant `A + k × I` in UTC, for whole numbers `k`, where `A` is the reset anchor time of day on 1970-01-01 and `I` is the reset interval. With the defaults (00:00 UTC, 24 hours), a reset happens every day at 00:00 UTC.
- **Next reset time:** the earliest reset time strictly later than now.
- **Due reset:** a reset is due when a reset time has passed and no reset has completed since that time.

### Settings
Read once at startup, validated only while demo mode is on (003a RF-2).

| Setting | Default | Valid range |
|---|---|---|
| Reset interval | 24 hours | 1 to 168 whole hours |
| Reset anchor time | 00:00 | UTC time of day, exactly `HH:MM` with `00` to `23` and `00` to `59` |
| Reset retry delay | 10 minutes | 1 to 1,440 minutes |
| Reset maximum duration | 30 seconds | 1 to 600 seconds |
| Lock wait timeout | 60 seconds | 0 to 3,600 seconds |

## Functional requirements

### Settings and single process
- RF-1: WHILE demo mode is on, IF any 003c setting is missing where required, unparsable or outside its valid range, THEN THE SYSTEM SHALL refuse to start with an error naming the setting.
- RF-2: WHILE demo mode is on, IF the server worker count from the `WEB_CONCURRENCY` environment variable is greater than one, THEN THE SYSTEM SHALL refuse to start immediately with the error `Demo mode requires a single process (WEB_CONCURRENCY=<n>)`.
- RF-3: WHILE demo mode is on, WHEN the system starts, THE SYSTEM SHALL acquire the demo process lock before seeding or serving.
  - IF another process holds it, THEN THE SYSTEM SHALL wait for it up to the lock wait timeout and SHALL not serve requests while waiting.
  - IF it is still held when the timeout ends, THEN THE SYSTEM SHALL refuse to start with the error `Demo mode requires a single process: another process holds the demo lock`.
- RF-4: THE SYSTEM SHALL tie the lock to the holding process's connection to the database, so a process that stops, crashes or loses its connection releases it automatically. A dead process SHALL never block the next start for longer than the database takes to notice the lost connection.
- RF-5: WHILE demo mode is on, IF a running process detects that it no longer holds the lock, THEN THE SYSTEM SHALL stop its demo jobs (the reset and the scheduler), log the reason, and exit with an error so the platform restarts it. The restarted process then follows RF-3.

### Startup order
- RF-6: WHILE demo mode is on, WHEN the system starts, THE SYSTEM SHALL proceed in this order, and SHALL start serving requests only after step 6:
  1. validate settings (003a RF-1 to RF-4, 003b RF-1, this spec's RF-1 and RF-2);
  2. acquire the lock (RF-3);
  3. seed (003a RF-5, RF-6);
  4. run a due reset if there is one (RF-9);
  5. start the scheduler;
  6. schedule the next reset.

  A failure in steps 1 to 3 refuses startup. A failure of the reset in step 4 is handled as in RF-12, and startup continues.

### Periodic reset
- RF-7: WHEN a reset runs, THE SYSTEM SHALL, as one unit:
  - delete every visitor service and its stored checks;
  - apply 003a RF-5 to the seeded services;
  - store the reset's completion time.

  The data changes and the completion time are committed together or not at all. Seeded and ordinary services and their stored checks are kept.
- RF-8: WHEN a reset commits, THE SYSTEM SHALL bring the schedules in line with the database (every active service has exactly one schedule, and no schedule exists for a deleted or inactive service), and SHALL then notify spec 003b so it clears its per-client attribution. A reset that does not commit triggers neither.
- RF-9: WHILE demo mode is on, WHEN a reset is due, THE SYSTEM SHALL run it as soon as possible: at startup, at its scheduled time, or later when a reset time was missed while the process was running (a paused machine, a clock jump forward or a late scheduler). Several missed reset times lead to a single reset.
- RF-10: WHILE a reset runs, THE SYSTEM SHALL keep serving read requests. A write request that arrives during a reset SHALL be processed after the reset ends, and its checks (caps, duplicates) SHALL be evaluated after the reset, not before.
- RF-11: IF a reset has not finished within the reset maximum duration, THEN THE SYSTEM SHALL abort it with no changes, and handle it as a failed reset (RF-12). No write waits longer than the reset maximum duration plus its own processing.
- RF-12: IF a reset fails or is aborted, THEN THE SYSTEM SHALL:
  - log the failure;
  - leave the database as it was before that reset;
  - bring the schedules in line with the database (RF-8);
  - keep serving requests and checking services;
  - try again after the reset retry delay, and keep retrying at that delay until a reset completes.
- RF-13: WHEN a check finishes for a service that no longer exists, deleted by a reset or by a delete request, THE SYSTEM SHALL store nothing for it, SHALL never attach its result to another service, and SHALL log it at warning level, not as an error.
- RF-14: THE SYSTEM SHALL compute reset times and due resets from a UTC clock, with time-zone-aware values. IF the stored completion time of the last reset is later than now (the clock moved back), THEN THE SYSTEM SHALL log a warning and treat no reset as due until the next reset time after now.

### Demo notice
- RF-15: WHILE demo mode is on, THE SYSTEM SHALL add two fields to the API info response (`GET /`) for every request it serves:
  - `demo_notice`: `Demo data resets every N hours.`, where N is the reset interval (`Demo data resets every 1 hour.` when N is 1);
  - `next_reset_at`: the next reset time, in UTC, ISO 8601 with seconds precision ending in `Z`, for example `2026-11-25T00:00:00Z`.

  While a reset is due but not yet complete (running, waiting to retry), `next_reset_at` shows the next scheduled retry or reset time. The existing fields don't change. While demo mode is off, neither field is present. As in spec 000, `GET /` keeps no response schema in the API schema document (see Decisions).

### Switching off and documentation
- RF-16: WHILE demo mode is off, THE SYSTEM SHALL not take the lock, reset or show the notice. Stored reset times are kept.
- RF-17: THE SYSTEM SHALL document:
  - the settings with defaults and ranges;
  - the reset schedule, catch-up, retry and maximum duration;
  - the notice;
  - the single-process design decision and the lock, with the deploy consequence: a new process waits for the old one up to the lock wait timeout, so overlapping deploys must stop the old process within that time, or the new one refuses;
  - the owner-approved exception to constitution principle 1.

  The detailed reference goes in the architecture document. The product SPEC and README summarize it and link there. `docs/constitution.md` gains an "Approved exceptions" list that points to the exceptions in specs 001 and 003c, without changing any principle.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change, except RF-13's log level for a check whose service was deleted. Today that is logged as an error, and on SQLite an orphan check can be stored. All existing tests pass with unchanged outcomes, and the API schema document is identical.
- RNF-2: Single-process design. Demo mode supports exactly one process per database. This is a deliberate design decision with a documented reason: the reset schedule, the scheduler and spec 003b's in-memory state all assume it. RF-2 to RF-5 enforce it.
- RNF-3: Every stored schema change goes through a versioned migration with owner approval in the plan (constitution principle 5): the last reset's completion time, and any stored state the lock needs. The spec 001 migration check stays green.
- RNF-4: **Benchmark** (measured and recorded in the validation notes; not a CI gate): a reset at maximum size completes in under 2 seconds on the containerized PostgreSQL. Maximum size is 003a's global cap of visitor services, each with one day of checks at the demo minimum interval.
- RNF-5: Time-based behavior (reset times, due resets, catch-up, retry, maximum duration, `next_reset_at`) is tested with an injectable clock, never by waiting.
- RNF-6: Behavior that depends on the database engine is verified on PostgreSQL by `postgres`-marked tests: the lock (wait, timeout, release on crash, loss), reset atomicity, foreign keys and RF-13, and time-zone-aware completion times.
- RNF-7: Schedule requirements (RF-8, RF-12) are verified with a real scheduler instance, not a mock.
- RNF-8: The spec 000 to 003a quality gates stay green.
- RNF-9: Automated tests cover, at minimum:
  - RF-1 and RF-2 refusals;
  - the lock: a second process waiting and then refusing, a second process starting after the first stops, release on crash, and loss leading to exit;
  - the RF-6 startup order, including seeding before the catch-up reset (history kept for an adopted visitor service);
  - reset contents and atomicity, including the completion time in the same commit;
  - schedule consistency after success, failure and abort;
  - the 003b notification only after a commit;
  - due-reset catch-up at startup and at runtime (missed time, clock jump), several missed times giving a single reset, retry after a failure, and the clock moving back;
  - writes during a reset evaluated after it;
  - the maximum duration abort;
  - RF-13 after a reset and after a delete;
  - RF-15 for N = 1 and N > 1, across an anchor boundary, while a retry is pending, and absent with demo mode off;
  - demo mode off changing nothing except RNF-1's exception.

## Owner-approved exception to constitution principle 1
Recorded 2026-11-23, owner-approved, and moved here from the original 003 on 2026-11-24. It is a standing exception to constitution principle 1 ("never ... drop ... a metric"), limited to demo mode:
- A reset deletes every visitor service together with all of its stored checks (RF-7).
- A check that finishes after its service was deleted is discarded (RF-13).

Seeded services' checks are never deleted, altered or fabricated (003a RF-8). Ordinary services' checks are never touched by a reset. Outside demo mode, the exception does not apply. `docs/constitution.md` lists this exception (RF-17).

## Edge cases
- The process is stopped across one or more reset times: one catch-up reset runs at the next startup, after seeding (RF-6, RF-9).
- A reset time passes while the machine is paused: the reset runs as soon as it resumes (RF-9).
- A reset fails because the database is briefly unavailable: it is retried every reset retry delay until it completes (RF-12). Visitor data stays until then.
- An overlapping deploy: the new process waits up to the lock wait timeout. If the platform stops the old process within that time, the new one takes over. If not, the new one refuses and the platform keeps or restarts it per its own policy. This is documented (RF-17).
- The database connection drops while running: the lock is lost, so the process stops its demo jobs and exits (RF-5). After a restart it waits for the lock and continues.
- A visitor creates a service a moment before a reset: the reset deletes it. A create arriving during the reset is processed after it, and is kept.
- A read request that overlaps a reset may see some data from before and some from after. Only that request is affected. Accepted (Decisions).
- A check of a visitor service is running when the reset deletes it: its result is discarded (RF-13).
- An interval that doesn't divide 24 hours (for example 5): reset times drift across times of day. They're still exact and shown by `next_reset_at`. Documented.

## Out of scope
- Running several processes or instances in demo mode.
- Configurable reset contents (it always deletes all visitor data).
- Notifying visitors other than through `GET /`.
- Hosting configuration for any specific platform.

## Completion criteria
- A second process started with demo mode on against the same database waits and then refuses. It starts once the first stops. A crashed first process doesn't block it.
- Resets run at the configured UTC times, catch up after downtime and missed times, retry after failure, abort past the maximum duration, and leave exactly one schedule per active service.
- Visitor data is removed; seeded and ordinary services keep their checks.
- `GET /` shows the notice and the correct `next_reset_at` in demo mode, and is unchanged with demo mode off.
- The RNF-4 benchmark is measured and recorded. The docs, including the constitution's exceptions list, are updated per RF-17. The quality gates are green in CI.

## Decisions made in revision
Items marked "owner" came from your answers; the others are mine, for you to review. Pass 1 decisions D7, D13, D16, D18, D23 and D24 of the original 003 are refined here.

- **F1 (owner) – lock during deploys:** a new process waits for the lock up to a timeout setting (default 60 s), then refuses. A process that loses the lock stops its demo jobs and exits (RF-3, RF-5).
  - Consequence: overlapping deploys must stop the old process within the timeout. A new process doesn't serve while it waits, so a platform that waits for the new one to be healthy before stopping the old one will time out. Documented (RF-17).
- **F2 (1.1) – worker detection:** only `WEB_CONCURRENCY` is visible to the app, so RF-2 checks it. `uvicorn` reads the same variable as its default worker count. An explicit `--workers` flag isn't visible, but extra workers still can't run demo mode at once, because each one must take the lock (RF-3).
- **F3 (1.2) – live vs dead processes:** the lock is tied to the database connection (RF-4), so a dead or disconnected process releases it. A running process that loses it exits (RF-5). The mechanism is the plan's choice. If it needs stored state, that state goes through the migration (RNF-3, which resolves 2.3 and 4.3).
- **F4 (1.3, 3.8) – startup order:** settings, lock, seed, due reset, scheduler, serve (RF-6). Seeding before the reset keeps an adopted visitor service's history. Failures before the reset refuse startup; a failed reset retries.
- **F5 (1.4) – completion time:** committed with the reset's data changes (RF-7).
- **F6 (1.5, 3.4, 4.2) – writes during a reset:**
  - writes wait for the reset and are evaluated after it (RF-10);
  - a reset longer than its maximum duration (default 30 s) is aborted (RF-11), which bounds the wait;
  - reads are never blocked.
- **F7 (3.10) – catch-up while running:** any due reset runs as soon as possible, not only at startup (RF-9). Several missed times give a single reset. A failed reset retries every 10 minutes by default (RF-12), not one interval later.
- **F8 (2.1) – 003b hook:** per-client attribution is cleared only after a reset commits (RF-8).
- **F9 (3.11) – late checks:** a check for a deleted service stores nothing and logs a warning, for both resets and normal deletes (RF-13). The log level is the only change with demo mode off (RNF-1).
- **F10 (3.13) – time zones:** UTC, time-zone-aware values, verified on PostgreSQL. A backward clock jump is handled by RF-14.
- **F11 (1.8) – intervals that don't divide 24:** allowed and documented as drifting. The formula is unchanged (owner decision D5/D7).
- **F12 (1.9) – busy workers:** removed as untestable. RF-9's "as soon as possible", with an injectable clock and a real scheduler, replaces it.
- **F13 (2.5) – `GET /` schema:** it keeps no response schema in the API schema document, as decided in spec 000 (D7 there), so the document stays identical (RNF-1). The fields are fixed here instead. This is a documented exception to AGENTS.md's "schemas define every response" for this one endpoint.
- **F14 (4.1) – constitution:** `docs/constitution.md` gets an "Approved exceptions" list pointing to specs 001 and 003c. No principle text changes.
- **F15 (2.7) – schedule tests:** verified with a real scheduler instance (RNF-7).
- **F16 – reads overlapping a reset:** may see mixed data. Accepted and documented (original 003 D18).

## Open questions
- (none)
