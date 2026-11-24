# 003d - Demo mode: single-process lock

Status: draft (created 2026-11-24 from 003c after `/sdd-clarify` pass 3)

## Context and goal
Demo mode keeps state that only works in one process: the reset schedule and its catch-up (003c), and the in-memory rate limits and per-client attribution (003b). Two processes running demo mode against the same database would reset twice, split the limits and duplicate checks.

Goal: while demo mode is on, exactly one process runs demo mode per database, and this is enforced:
- a second process waits for the first to stop, up to a timeout, and then refuses;
- a process that loses its claim stops and exits;
- a dead process never blocks the next one for long.

This spec depends on 003a. 003c and 003b depend on it. Implementation order: 003a, then 003d, then 003c, then 003b. All four are needed before a public deployment.

## Users
- **Owner/operator:** deploys new versions without two processes ever running demo mode at once, and gets a clear error when the setup can't guarantee that.

## User stories
- As the owner, when I deploy a new version, the new process takes over once the old one stops, or refuses with a clear error.
- As the owner, a crashed process doesn't block the next start beyond a known bound.
- As the owner, a misconfiguration (several workers, an unsupported database) is refused at startup with an error that says what to change.

## Definitions
- **Demo lock:** an exclusive claim on the database, held by the process running demo mode for as long as it runs. The database itself releases it when the holding process's dedicated connection ends.
- **Lock connection:** a database connection that holds the demo lock. It is used only for that and is never shared with request handling or checks.
- **Heartbeat:** a periodic check by the holder that its lock connection is alive and still holds the lock.

### Settings
Read once at startup, validated only while demo mode is on (003a RF-2).

| Setting (environment variable) | Default | Valid values |
|---|---|---|
| Lock wait timeout (`DEMO_LOCK_WAIT_SECONDS`) | 60 | integer 0 to 3,600 |
| Heartbeat interval (`DEMO_LOCK_HEARTBEAT_SECONDS`) | 10 | integer 1 to 300 |
| Lock connection keepalive (`DEMO_LOCK_KEEPALIVE_SECONDS`) | 30 | integer 5 to 600 |

## Functional requirements
- RF-1: WHILE demo mode is on, IF any 003d setting is unparsable or outside its valid values, THEN THE SYSTEM SHALL refuse to start with an error naming the environment variable.
- RF-2: WHILE demo mode is on, IF the database is not PostgreSQL, THEN THE SYSTEM SHALL refuse to start with the error `Demo mode requires PostgreSQL`.
- RF-3: WHILE demo mode is on, THE SYSTEM SHALL read `WEB_CONCURRENCY`. Unset or empty means 1.
  - IF it is not a whole number of at least 1, THEN THE SYSTEM SHALL refuse to start with an error naming it.
  - IF it is greater than 1, THEN THE SYSTEM SHALL refuse to start immediately with `Demo mode requires a single process (WEB_CONCURRENCY=<n>)`.
- RF-4: WHILE demo mode is on, WHEN the system starts, THE SYSTEM SHALL try to take the demo lock on a lock connection, as step 2 of 003c's startup order.
  - IF another process holds it, THEN THE SYSTEM SHALL keep trying until the lock wait timeout ends, without serving requests, and SHALL stop promptly (within one second) if asked to shut down while waiting.
  - IF the lock is still held when the timeout ends, THEN THE SYSTEM SHALL refuse to start with `Demo mode requires a single process: another process holds the demo lock`.
- RF-5: THE SYSTEM SHALL hold the lock only through the lock connection, so the database releases it when that connection ends:
  - **Clean stop:** released at once.
  - **Crash:** released as soon as the database closes the connection.
  - **Lost network:** released within about the lock connection keepalive time, because the lock connection uses TCP keepalive at that interval.
- RF-6: WHILE demo mode is on and the lock is held, THE SYSTEM SHALL run a heartbeat every heartbeat interval. IF the lock connection is broken or the lock is no longer held, THEN THE SYSTEM SHALL, within one heartbeat interval plus 5 seconds of the loss:
  - stop its demo jobs (the reset of 003c and the scheduler);
  - log the reason;
  - end the server process with a non-zero exit status, so the platform restarts it. The restarted process then follows RF-4.
- RF-7: WHILE demo mode is off, THE SYSTEM SHALL not take the lock, read `WEB_CONCURRENCY` or require PostgreSQL.
- RF-8: THE SYSTEM SHALL document:
  - the single-process design decision and its reason;
  - the settings;
  - that demo mode requires PostgreSQL directly, not through a transaction-pooling proxy, because the lock lives on one connection;
  - that `WEB_CONCURRENCY` must be 1, and that some platforms set it;
  - that an explicit server `--workers` flag above 1 makes the extra processes wait and then refuse in a restart loop;
  - the deploy consequence: a new process waits up to the lock wait timeout for the old one to stop, so overlapping deploys must stop the old process within that time, or the new one refuses;
  - the worst-case startup time (003c RF-3).

  The detailed reference goes in the architecture document. The README "Running a public demo" section summarizes it.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change. All existing tests pass with unchanged outcomes, and the API schema document is identical.
- RNF-2: The lock needs no stored schema. It lives in the database's own lock facility, so there is no migration.
- RNF-3: The lock connection is not taken from the pool used by requests and checks, so their capacity is unchanged.
- RNF-4: The lock behavior (wait, timeout, release on clean stop and on crash, heartbeat loss leading to exit, shutdown during the wait) is verified on PostgreSQL by `postgres`-marked tests. The default SQLite suite uses a stand-in for the lock, and RF-2 is tested there.
- RNF-5: Time-based behavior (the wait timeout, the heartbeat) is tested with an injectable clock or short configured values, never with the defaults.
- RNF-6: The spec 000 to 003a quality gates stay green.

## Edge cases
- An overlapping deploy: the new process waits up to the lock wait timeout. If the platform stops the old process within that time, the new one takes over. If not, the new one refuses, and the platform keeps or restarts it per its own policy.
- The platform waits for the new process to be healthy before stopping the old one: the new one doesn't serve while waiting, so it times out and refuses. Documented. Such platforms need a stop-first deploy strategy for demo mode.
- The database restarts or fails over: the lock connection breaks, the heartbeat detects it, and the process exits and restarts (RF-6).
- A platform sets `WEB_CONCURRENCY` above 1: startup refuses with a message naming it. The operator sets it to 1.
- The documented development command with `--reload`: one serving process, which is supported. A reload restarts it and it retakes the lock.
- A transaction-pooling proxy between the app and PostgreSQL: the lock can't be held reliably. This is unsupported and documented. It isn't detected.

## Out of scope
- Running several processes or instances in demo mode, or electing a leader among them.
- Databases other than PostgreSQL in demo mode.
- Detecting a server `--workers` flag, which the application can't see.

## Completion criteria
- A second process started with demo mode on against the same database waits, then refuses. It starts once the first stops. A killed first process doesn't block it.
- Breaking the holder's lock connection makes it exit within the heartbeat bound, and a restart retakes the lock.
- `WEB_CONCURRENCY` above 1 and a non-PostgreSQL database each refuse startup with the documented message.
- The docs are updated per RF-8. The quality gates are green in CI.

## Decisions made in revision
Earlier decisions F1 to F3 of 003c (pass 2) are refined here. Items marked "owner" came from your answers.

- **I1 (owner, decision 1) – split:** the lock is its own spec, split from 003c.
- **I2 (owner, decision 2) – lock scope:**
  - demo mode requires PostgreSQL, and other databases refuse startup (RF-2);
  - the lock is held on a dedicated connection with a heartbeat, which is a setting (RF-6), so detection is bounded and testable (findings 1.1 and 4.2 of 003c);
  - SQLite tests use a stand-in.
- **I3 (1.9, 3.1) – dead holders:** release is bounded by the lock connection's TCP keepalive, which is a setting (RF-5). Transaction-pooling proxies are unsupported and documented. The lock connection doesn't use the shared pool (RNF-3).
- **I4 (1.7) – `WEB_CONCURRENCY`:** unset or empty means 1, invalid values refuse, and platforms that set it are documented.
- **I5 (2.6) – shutdown during the wait:** honored within one second (RF-4).
- **I6 (1.1) – exiting from a background check:** RF-6 requires the server process to end with a non-zero status. How is left to the plan.
- **I7 (4.4) – no schema:** the lock uses the database's lock facility (RNF-2), so 003c's open "any stored state the lock needs" is closed.
- **I8 (3.8) – `--workers` and `--reload`:** documented. `--workers` above 1 causes a restart loop that can't be detected; `--reload` is supported.

## Open questions
- (none)
