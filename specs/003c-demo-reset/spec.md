# 003c - Demo mode: periodic reset and notice

Status: draft (revised 2026-11-24 after `/sdd-clarify` pass 5)

## Context and goal
Spec 003a adds demo mode: seeded services, protection and caps on visitor services. Nothing returns the demo to a known state, so after a few days it shows whatever visitors left behind.

Goal: while demo mode is on, the API removes everything visitors created at fixed UTC slots. It catches up whenever a reset was missed, retries a failed one, never blocks writes for long, and tells visitors when the next reset happens.

This spec depends on 003a and 003d (the start command and the single process). 003b depends on it. Implementation order: 003a, then 003d, then 003c, then 003b. All four are needed before a public deployment.

## Users
- **Visitor:** finds the demo in a known state, and can see from `GET /` that it resets, and when.
- **Owner/operator:** doesn't clean up after visitors, even after downtime or a failed reset.

## User stories
- As a visitor, I can see from `GET /` that the demo resets, and when.
- As the owner, the demo removes visitor data at fixed UTC slots, every day by default, catching up after downtime and retrying after a failure.
- As a visitor writing during a reset, I wait briefly or get a clear "try again shortly" answer, never a half-applied change.

## Definitions
- **Write request:** as defined in 003b: a request with method POST, PUT, PATCH or DELETE, on any path.
- **Write in progress:** a write request from the moment it is admitted (past 003b's rate check and this spec's gate) until its response is produced.
- **Reset slots:** every instant `A + k × I` in UTC, for whole numbers `k`, where `A` is the reset anchor time of day on 1970-01-01 and `I` is the reset interval.
  - With the defaults (00:00 UTC, 24 hours), there is a slot every day at 00:00 UTC.
  - An interval that doesn't divide 24 makes slot times drift across the day. With 168 hours, slots fall on Thursdays, because 1970-01-01 was a Thursday.
- **Current slot:** the latest slot at or before now. **Next slot:** the earliest slot strictly after now.
- **Last completed slot:** the slot that the most recent completed reset served. It is stored, and is empty on a database where no reset has completed.
- **Due reset:** a reset is due when the last completed slot is empty or earlier than the current slot.
- **Reset states:** idle (no reset due), due (due but not started), running, retry pending (a failed reset waiting for its retry).

### Settings
Read once at startup, validated only while demo mode is on (003a RF-2). Integers use decimal digits only.

| Setting (environment variable) | Default | Valid values |
|---|---|---|
| Reset interval (`DEMO_RESET_INTERVAL_HOURS`) | 24 | integer 1 to 168 |
| Reset anchor time (`DEMO_RESET_ANCHOR`) | `00:00` | UTC time of day, exactly `HH:MM`, `00`–`23` and `00`–`59` |
| Due-reset poll interval (`DEMO_RESET_POLL_SECONDS`) | 60 | integer 10 to 3,600 |
| Reset retry delay (`DEMO_RESET_RETRY_MINUTES`) | 10 | integer 1 to 1,440 |
| Reset maximum duration (`DEMO_RESET_MAX_SECONDS`) | 30 | integer 1 to 600 |
| Write wait during a reset (`DEMO_RESET_WRITE_WAIT_SECONDS`) | 5 | integer 1 to 60 |
| Maximum waiting writes (`DEMO_RESET_MAX_WAITING_WRITES`) | 10 | integer 1 to 100 |

## Functional requirements

### Settings and startup
- RF-1: WHILE demo mode is on, IF any 003c setting is unparsable or outside its valid values, THEN THE SYSTEM SHALL refuse to start. 003d's start command reports this together with the other settings errors.
- RF-2: WHILE demo mode is on, after 003d's start command has validated the settings, taken the lock and run the migrations (003d RF-4), THE SYSTEM SHALL, at application startup and in this order:
  1. check the start marker (003d RF-2);
  2. seed (003a RF-5, RF-6), committed as one transaction;
  3. run a due reset if there is one (RF-6);
  4. start the scheduler, which schedules every active service with its current interval and runs the first checks of seeded services (003a RF-8);
  5. start the demo jobs (the slot job, the poll and any retry).

  It starts serving requests only after step 5. A failure in steps 1 and 2 refuses startup. A failed reset in step 3 is handled as in RF-9, and startup continues. At startup, step 4 is the schedule reconciliation.
- RF-3: The maximum time from start to serving, with default settings, is about: 003d's lock wait + the migrations + one resolution timeout (seed validation) + less than twice the reset maximum duration (RF-8). THE SYSTEM SHALL document it. 003d RF-9 sizes the container's healthcheck start period from it.

### Reset
- RF-4: WHEN a reset starts, THE SYSTEM SHALL fix the slot it serves as the current slot at that moment, then:
  - close the gate to new writes (RF-7);
  - wait for writes in progress to finish, up to the write wait setting. IF they haven't finished by then, THEN the attempt is a failed reset (RF-9) and the gate opens again;
  - in one database transaction: delete every visitor service and, explicitly, its stored checks (not relying on the database's cascade), apply 003a RF-5 to the seeded services, and store the fixed slot as the last completed slot;
  - just before committing, confirm that this process still holds the demo lock (003d). If not, roll back, and the attempt is a failed reset.

  The data changes and the slot are committed together or not at all. Seeded and ordinary services and their stored checks are kept.
- RF-5: WHEN a reset's transaction commits, THE SYSTEM SHALL:
  - bring the schedules in line with the database: every active service has exactly one schedule, at its current check interval, and no schedule exists for a deleted or inactive service. Schedules that are already correct are left untouched, so their timers don't restart. Reconciliation and the schedule changes made by create, update and delete requests take turns, one at a time, and reconciliation reads the database only after it has its turn, so it never undoes a newer change. Seeded services created, adopted or reactivated by the reset get their immediate first check (003a RF-8);
  - notify 003b, which clears its per-client attribution in memory;
  - open the gate.

  The reset ends after these steps. IF reconciling the schedules fails, THEN THE SYSTEM SHALL log an error, open the gate anyway, and retry the reconciliation at every poll until it succeeds. A failed reset also ends by reconciling, with the same rule, and by opening the gate, with no 003b notification.
- RF-6: WHILE demo mode is on, THE SYSTEM SHALL check whether a reset is due:
  - at startup (RF-2 step 3);
  - at each slot;
  - at every due-reset poll interval;
  - when a retry delay ends.

  WHEN a reset is due and none is running, THE SYSTEM SHALL start one. At most one reset runs at a time. A slot or poll that arrives during a reset, or while a retry is pending, starts nothing; the retry, or the next check after the reset ends, decides. Several missed slots lead to a single reset. The demo jobs run in their own scheduler, separate from the check jobs, so busy check workers never delay or skip them. A demo job that runs late, by up to the poll interval, still runs. Several late runs of the same job collapse into one. A slot missed while running (a paused machine, a clock jump forward) is caught up at most one poll interval late.
- RF-7: WHILE the gate is closed, THE SYSTEM SHALL keep serving non-write requests normally. A write request that arrives SHALL:
  - be checked for rate limits first (003b), so a 429 is never held;
  - IF the number of writes already waiting has reached the maximum waiting writes, THEN get `503` at once;
  - otherwise wait up to the write wait setting for the gate to open. IF it opens, THEN the write is processed normally, with every check (404, 403, caps, duplicates) evaluated after the reset. IF not, THEN `503`.

  A `503` has `Retry-After` equal to the write wait setting, and `{"detail": "Demo reset in progress. Retry in a few seconds."}`. The write isn't processed. The gate and the wait sit right after 003b's rate check, before routing, and the wait doesn't occupy a request worker. So waiting writes don't reduce the capacity available to non-write requests.
- RF-8: THE SYSTEM SHALL give every statement of a reset's transaction a database-enforced timeout equal to the reset maximum duration, SHALL set the session's idle-in-transaction timeout to the same value (so a stall between statements is also rolled back by the database), and SHALL check the elapsed time, measured from the transaction's first statement, between statements. IF a statement times out or the elapsed time exceeds the maximum, THEN the transaction is rolled back and the reset is a failed reset (RF-9). A reset therefore holds the database for less than twice the maximum duration. The system SHALL not interrupt a reset in any other way, except that 003d RF-7's exit on lock loss ends the connection, which rolls the transaction back.
- RF-9: IF a reset fails (an error, a timeout, RF-4's write wait or RF-8's limit), THEN THE SYSTEM SHALL:
  - log the failure;
  - leave the database as it was before that reset;
  - end the reset as in RF-5;
  - keep serving and checking;
  - retry after the reset retry delay, and keep retrying at that delay while a reset is still due.
- RF-10: WHEN a check finishes for a service that no longer exists, deleted by a reset or by a delete request, THE SYSTEM SHALL store nothing for it, and SHALL log it at warning level, not as an error. This applies whether demo mode is on or off (RNF-1). On PostgreSQL, service ids are never reused, so a late result can never attach to another service. On SQLite (development and tests only, since demo mode requires PostgreSQL per 003d), the check confirms the service still exists in the same transaction before storing. Id reuse after a delete remains possible there, which is documented and accepted.
- RF-11: THE SYSTEM SHALL compute slots and due resets only from the application's UTC clock, using time-zone-aware values. The database clock is not used. At every due check, IF the stored last completed slot is later than now (the clock moved back), THEN THE SYSTEM SHALL log a warning and replace it with the current slot without running a reset. This replacement takes turns with resets, so it never overlaps one. Visitor data from before the clock jump stays until the next slot. Accepted.

### Demo notice
- RF-12: WHILE demo mode is on, THE SYSTEM SHALL add two fields to the API info response (`GET /`) for every request:
  - `demo_notice`: `Demo data resets every N hours.`, where N is the reset interval (`Demo data resets every 1 hour.` when N is 1);
  - `next_reset_at`: in UTC, ISO 8601 with seconds precision ending in `Z`, rounded up to the next whole second:
    - when idle: the next slot;
    - when due or running: the current time;
    - when a retry is pending: the retry time.

  The state comes from the process's memory (the last completed slot is loaded at startup and updated on every commit, plus the reset state), so `GET /` never queries the database and still answers when the database is down. The existing fields don't change. While demo mode is off, neither field is present. `GET /` keeps no response schema in the API schema document, as decided in spec 000, so the document stays identical.

### Shutdown, switching off and documentation
- RF-13: WHEN the process stops (a stop signal, or 003d's exit on lock loss) during a reset, THE SYSTEM SHALL let the database roll back the uncommitted transaction. The slot is not stored, so the next start catches up (RF-6). Writes that were waiting are not processed.
- RF-14: WHILE demo mode is off, THE SYSTEM SHALL not reset, poll or show the notice. The stored last completed slot is kept.
- RF-15: THE SYSTEM SHALL document:
  - the settings;
  - the slot formula and its drift and Thursday consequences;
  - catch-up, the poll, retry, the maximum duration and the actual bound (less than twice the maximum);
  - the write wait, the waiting-writes limit and the 503;
  - that 503'd writes still count toward 003b's write limit;
  - the notice;
  - the startup time (RF-3);
  - that a reset runs at the first start and when demo mode is turned on after a gap;
  - the owner-approved exception to constitution principle 1.

  The detailed reference goes in the architecture document. The product SPEC and README summarize it and link there. In addition:
  - `docs/constitution.md` gains an "Approved exceptions" list that points to the exceptions in specs 001 and 003c. No principle text changes.
  - AGENTS.md notes that `GET /` is the one response without a schema, by decision of spec 000.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change, except RF-10:
  - a check for a deleted service is logged as a warning instead of an error;
  - on SQLite, its result is no longer stored as an orphan.

  All existing tests pass with unchanged outcomes, and the API schema document is identical.
- RNF-2: The last completed slot is the only stored schema change in this spec. It is made through a versioned migration with owner approval in the plan (constitution principle 5). The migration is additive, and 003d runs migrations only after taking the lock. The spec 001 migration check stays green.
- RNF-3: **Benchmark** (measured and recorded in the validation notes; not a CI gate): with default settings, a reset at maximum size completes in under 2 seconds on the containerized PostgreSQL. Maximum size is 20 visitor services, each with one day of checks at 300 s, so 288 checks each, plus the seeded services and their history.
- RNF-4: Time-based behavior (slots, due resets, the poll, retry, the write wait, `next_reset_at`) is tested with an injectable clock, never by waiting.
- RNF-5: Behavior that depends on the database engine is verified on PostgreSQL by `postgres`-marked tests:
  - reset atomicity and the slot in the same commit;
  - the per-statement timeout and the elapsed-time rollback;
  - RF-10 with a check in flight;
  - time-zone-aware slot storage.
- RNF-6: Schedule requirements (RF-5, RF-9) are verified with a real scheduler instance, not a mock.
- RNF-7: The spec 000 to 003a and 003d quality gates stay green.
- RNF-8: Automated tests cover, at minimum:
  - RF-1, and the RF-2 order, including seeding before the catch-up reset (history kept for an adopted visitor service);
  - reset contents and atomicity;
  - the slot fixed at start;
  - the drain of writes in progress, and its timeout;
  - schedule consistency, including intervals, after success and failure, and reconciliation failure with the gate opening;
  - the 003b notification only after a commit;
  - due-reset detection at the first start, after downtime, at a slot, by the poll after a missed slot, as a single reset for several missed slots, and with check workers busy;
  - retry after a failure, and the RF-8 rollback;
  - writes during a reset: completed within the wait, 503 after the wait, 503 at once when too many are waiting, and a 429 never held;
  - RF-10 after a reset and after a delete;
  - RF-11 with the clock moving back;
  - RF-12 in each state, and absent with demo mode off;
  - RF-13 shutdown during a reset;
  - demo mode off changing nothing except RNF-1's exceptions.

## Owner-approved exception to constitution principle 1
**Owner approval, 2026-11-23:** "record the reset's bulk deletion of visitor services and their checks as an owner-approved exception, in the same format as spec 001's. Seeded history is never deleted or fabricated."

This is a standing exception to constitution principle 1 ("never ... drop ... a metric"), limited to demo mode. A reset deletes every visitor service together with all of its stored checks (RF-4). Seeded services' checks are never deleted, altered or fabricated (003a RF-9). Ordinary services' checks are never touched by a reset. Outside demo mode, the exception does not apply. Not storing a check whose service was already deleted (RF-10) is not part of this exception: the check belongs to no existing service, and PostgreSQL already refuses it today.

**Owner approval for the constitution list, 2026-11-24:** the question was "Do you approve adding an 'Approved exceptions' list to `docs/constitution.md`, pointing to specs 001 and 003c, without changing any principle text?" The answer was "yes" (pass-3 decision 4). RF-15 implements it.

## Edge cases
- The first start of demo mode on a database: the last completed slot is empty, so a reset runs at once. A fresh database has no visitor data, so nothing is lost.
- Demo mode is turned on again after a gap: a reset runs at startup and removes visitor services left from before. They keep origin "visitor", per 003a.
- The process is stopped across one or more slots: one catch-up reset runs at the next startup, after seeding.
- A slot passes while the machine is paused: the poll catches it at most one poll interval after it resumes.
- A process in a crash loop: each start runs a reset only if one is due. Once one completes, restarts don't repeat it until the next slot.
- A reset fails because the database is briefly unavailable: it retries every reset retry delay while still due. Reconciling the schedules also retries at every poll. If the database is down at a restart, 003d's start command refuses and the platform restarts it.
- A slow write is in progress when a reset starts: the reset waits up to the write wait setting. If the write still hasn't finished, that attempt fails and retries later, so a stalled write never blocks writes for long.
- A slot passes while a long reset runs: the reset stores the slot fixed at its start. The due check after it ends sees the newer slot and runs one more reset.
- Check jobs inserting results while a reset deletes their service: they wait for the reset's transaction, which RF-8 bounds, then store nothing (RF-10).
- A visitor retries a 503 at once several times: each attempt counts toward 003b's write limit, so it may get a 429. Documented.
- A read request that overlaps a reset may see some data from before and some from after. Only that request is affected. Accepted.
- Demo mode is switched off and on again within the same slot: no reset is due, so visitor services left from before stay until the next slot. Accepted and documented.
- The lock is lost while a reset runs: the reset checks the lock just before committing (RF-4), and the process exits soon after (003d RF-7). A commit can only slip through if the lock is lost in the instant between that check and the commit. Accepted as part of 003d's documented overlap.

## Out of scope
- The start command and lock (003d), rate limits (003b), seeding and caps (003a).
- Configurable reset contents.
- Notifying visitors other than through `GET /`.

## Completion criteria
- Resets run at the configured UTC slots, catch up after downtime and missed slots (also with busy check workers), retry after failure, roll back past the maximum duration, and leave exactly one schedule per active service at its current interval.
- Visitor data is removed. Seeded and ordinary services keep their checks.
- Writes during a reset either complete after it or get the 503 within the write wait. Excess waiting writes get the 503 at once.
- `GET /` shows the notice and the correct `next_reset_at` in each state in demo mode, and is unchanged with demo mode off.
- The RNF-3 benchmark is measured and recorded. The docs, the constitution's exceptions list and the AGENTS.md note are updated per RF-15. The quality gates are green in CI.

## Decisions made in revision
Earlier decisions (F series where still applicable, and J1 to J16) still apply unless refined here. Items marked "owner" came from your answers ("go with your recommendations", 2026-11-24).

- **N1 (owner, decision 3) – bounding the reset on PostgreSQL 15:** there is no per-transaction timeout before PostgreSQL 17 (finding 1.1). Each statement gets a database-enforced timeout, and elapsed time is checked between statements (RF-8). The honest bound, less than twice the maximum duration, is stated and documented.
- **N2 (1.2, 4.1) – draining writes in progress:** bounded by the write wait setting. A drain that doesn't finish fails the attempt, which retries later, so a stalled write can't keep the gate closed. "In progress" is defined.
- **N3 (1.3) – demo jobs:** run separately from check jobs, so busy check workers never delay or skip them, and a late run still runs (RF-6). 003d stops them on lock loss.
- **N4 (1.4, 2.1) – `next_reset_at`:** defined for every state. Due and running show the current time. Retry pending shows the retry time, because no slot starts a reset while a retry is pending.
- **N5 (1.5) – which slot is stored:** the slot fixed when the reset starts. A slot that passes during the reset causes one more reset afterwards.
- **N6 (1.6) – waiting writes:** at most 10 wait at once (a setting); more get 503 at once. So waiting writes can't starve other requests.
- **N7 (1.7) – startup time:** includes the migrations and the actual reset bound. 003d sizes the healthcheck (003d RF-9).
- **N8 (1.8) – clock-back check:** runs at every due check (RF-11).
- **N9 (1.9) – reconciliation:** includes each schedule's interval. At startup it is the scheduler start (RF-2 step 4).
- **N10 (3.1) – failure after a commit:** a reconciliation failure is logged, the gate opens anyway, and reconciliation retries at every poll. The 003b notification is in memory and can't fail.
- **N11 (3.2) – shutdown during a reset:** the database rolls back the transaction, the slot isn't stored, and the next start catches up (RF-13).
- **N12 (3.3) – 503s count toward 003b's write limit:** accepted and documented.
- **N13 (3.4) – drifting slots:** the goal now says "fixed UTC slots", and the drift for intervals that don't divide 24 is documented.
- **N14 (4.2) – constitution approval:** the exact question and answer are quoted in the exception section.
- **N15 (2.2 of 003d) – interruption by lock loss:** allowed explicitly (RF-8, RF-13).

- **S1 (1.1) – gate placement:** right after the rate check, before routing, without occupying a request worker, so the capacity claim holds.
- **S2 (1.2, 1.6) – reconciliation vs requests:** reconciliation and request schedule changes take turns, and reconciliation reads the database only after it has its turn. Correct schedules are left untouched, so timers don't restart.
- **S3 (1.3) – notice state:** kept in memory, so `GET /` never queries the database.
- **S4 (1.4, 2.1) – elapsed time and stalls:** elapsed time is measured from the transaction's first statement. An idle-in-transaction timeout makes the database also roll back stalls between statements, so the bound holds even if the process hangs.
- **S5 (1.5) – demo jobs:** their own scheduler. A run up to one poll interval late still runs, and repeated late runs collapse into one.
- **S6 (2.2) – lock loss vs commit:** the lock is checked just before committing. The remaining instant is part of 003d's documented overlap.
- **S7 (3.1) – clock-back replacement:** takes turns with resets. The leftover visitor data is accepted.
- **S8 (3.2) – SQLite:** checks are deleted explicitly, and a late check confirms its service still exists before storing. Id reuse on SQLite is accepted (development and tests only).
- **S9 (3.3) – off and on within one slot:** leftovers stay until the next slot. Accepted and documented.
- **S10 (3.4) – zero values:** the write wait and the waiting-writes limit now start at 1.
- **S11 (4.1) – `Retry-After`:** equals the write wait setting instead of a hardcoded 5.
- **S12 (3.3 of 003a) – seeds created at a reset:** get their immediate first check (RF-5).

## Open questions
- (none)
