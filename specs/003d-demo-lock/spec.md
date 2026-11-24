# 003d - Demo mode: project start command and single-process lock

Status: draft (revised 2026-11-24 after `/sdd-clarify` pass 4; now also owns the project start command)

## Context and goal
Demo mode keeps state that only works in one process: the reset schedule and its catch-up (003c), and the in-memory rate limits and per-client attribution (003b). Two processes running demo mode against the same database would reset twice, split the limits and duplicate checks.

Clarify pass 4 verified that the server (uvicorn 0.42.0) only records a stop signal during application startup and acts on it after startup finishes. So a lock wait inside the application can't honor a stop request, and the container's current `sh -c` command may not pass the stop signal on to the server at all. The lock is therefore taken by the **project start command**, before the server starts.

Goal: one project start command that runs the API in every mode. While demo mode is on, it guarantees exactly one process per database:
- a second process waits for the first to stop, up to a timeout, and then refuses;
- migrations run only after the lock is held;
- a process that loses the lock stops its demo jobs and exits;
- a dead process never blocks the next one for long.

This spec depends on 003a. 003c and 003b depend on it. Implementation order: 003a, then 003d, then 003c, then 003b. All four are needed before a public deployment.

## Users
- **Owner/operator:** deploys new versions without two processes ever running demo mode at once, stops the container cleanly, and gets a clear error when the setup can't guarantee a single process.

## User stories
- As the owner, when I deploy a new version, the new process takes over once the old one stops, or refuses with a clear error.
- As the owner, stopping the container stops the process within seconds, even while it waits for the lock.
- As the owner, a crashed process doesn't block the next start beyond a known bound.
- As the owner, a misconfiguration (several workers, auto-reload, an unsupported database, a different start command) is refused with an error that says what to change.

## Definitions
- **Project start command:** the single command, provided by the project, that starts the API. The container image and the documentation use it.
- **Demo lock:** an exclusive claim on the database, held by the process running demo mode for as long as it runs. The database releases it when the holding process's lock connection ends.
- **Lock connection:** a database connection opened by the project start command only to hold the demo lock. It is never shared with request handling or checks.
- **Demo jobs:** the reset slot job, the due-reset poll and the reset retry (003c), and the check scheduler.
- **Heartbeat:** a periodic query on the lock connection that confirms the connection is alive and the lock is still held.

### Settings
Read once at startup, validated only while demo mode is on (003a RF-2). Integers use decimal digits only: no sign, no decimal point, no separators, no spaces.

| Setting (environment variable) | Default | Valid values |
|---|---|---|
| Lock wait timeout (`DEMO_LOCK_WAIT_SECONDS`) | 60 | integer 0 to 3,600 (0 = one attempt only) |
| Heartbeat interval (`DEMO_LOCK_HEARTBEAT_SECONDS`) | 10 | integer 1 to 300 |
| Heartbeat and connect timeout (`DEMO_LOCK_TIMEOUT_SECONDS`) | 5 | integer 1 to 60, and below the heartbeat interval |
| Dead-connection detection (`DEMO_LOCK_KEEPALIVE_SECONDS`) | 30 | integer 10 to 600 |
| Lock name (`DEMO_LOCK_NAME`) | `service-monitoring-api-demo` | 1 to 100 characters of letters, digits, `-` and `_` |

`WEB_CONCURRENCY` is not a demo setting. It is the server's own worker-count variable, read by the start command as described in RF-3.

## Functional requirements

### The project start command
- RF-1: THE SYSTEM SHALL provide a project start command that, in every mode:
  - reads all settings with the application's own settings rules, including the `.env` file;
  - runs the database migrations;
  - then runs the server in the same process, as a single worker, without auto-reload, on host `0.0.0.0` and the port from `PORT` (default 8000).

  While demo mode is off, the server options are the ones the container uses today. 003b adds its server options for demo mode. The container image starts this command directly as its main process, so it receives stop signals itself (RNF-5).
- RF-2: WHILE demo mode is on, IF the application was not started by the project start command (for example, a platform overrides the start command, or the server is run with auto-reload or several workers), THEN THE SYSTEM SHALL refuse to start with `Demo mode must be started with the project start command (python -m app.serve)`. The application recognizes the command by a marker the command sets in its own process before starting the server. Processes spawned by auto-reload or extra workers don't have it.
- RF-3: WHILE demo mode is on, THE SYSTEM SHALL read `WEB_CONCURRENCY` in the start command before anything else. Unset or empty means 1.
  - IF it is not decimal digits for a number of at least 1, THEN THE SYSTEM SHALL refuse with an error naming it.
  - IF it is greater than 1, THEN THE SYSTEM SHALL refuse with `Demo mode requires a single process (WEB_CONCURRENCY=<n>)`.
- RF-4: WHILE demo mode is on, THE SYSTEM SHALL do the following in the start command, in this order, before starting the server:
  1. validate all demo settings (003a, 003b, 003c and this spec), reporting every invalid one at once;
  2. check `WEB_CONCURRENCY` (RF-3);
  3. refuse with `Demo mode requires PostgreSQL` if the database is not PostgreSQL;
  4. take the demo lock (RF-5);
  5. run the migrations;
  6. start the server.

  A failure at any step ends the process with a non-zero status and a clear error.

### Taking and holding the lock
- RF-5: WHILE demo mode is on, THE SYSTEM SHALL open the lock connection with the connect timeout and try to take the demo lock, identified by the lock name and the database. Behavior while trying:
  - IF the lock is held by another process, or the database can't be reached, THEN THE SYSTEM SHALL try again every second until the lock wait timeout ends (one attempt when it is 0).
  - IF it gets a stop signal (SIGTERM or SIGINT) while waiting, THEN THE SYSTEM SHALL exit within one second without taking the lock or running migrations.
  - IF the timeout ends first, THEN THE SYSTEM SHALL refuse with `Demo mode requires a single process: another process holds the demo lock`, or `Demo mode could not reach the database to take the demo lock` when the database was never reached.
- RF-6: THE SYSTEM SHALL hold the lock only through the lock connection, so the database releases it when that connection ends:
  - **Clean stop or crash:** released as soon as the connection closes.
  - **Lost network:** the lock connection asks the database to probe it, so the database notices a dead client within about the dead-connection detection time and releases the lock. Over a local Unix socket a network loss can't happen, and a process death closes the socket.
- RF-7: WHILE demo mode is on and the lock is held, THE SYSTEM SHALL run a heartbeat every heartbeat interval, with the heartbeat timeout. IF one heartbeat fails (an error, a timeout, or the lock no longer held), THEN THE SYSTEM SHALL at once:
  - stop the demo jobs;
  - log the reason;
  - end the server process with a non-zero exit status within the heartbeat timeout plus 2 seconds, so the platform restarts it. The restarted process then follows RF-4.

  A reset that is running is rolled back by the database when the connection ends. This is the one interruption 003c RF-8 allows.
- RF-8: WHILE demo mode is off, THE SYSTEM SHALL not take the lock, read `WEB_CONCURRENCY`, require PostgreSQL or check the start marker. The start command still runs the migrations and the server as described in RF-1.

### Container and documentation
- RF-9: THE SYSTEM SHALL change the container so that:
  - it starts the project start command directly as its main process, not through a shell, so stop signals reach it;
  - its healthcheck start period is at least the worst-case startup with default settings: the lock wait timeout + the migrations + 003a's seed validation + 003c's maximum reset time, which is 120 seconds with the defaults.
- RF-10: THE SYSTEM SHALL document:
  - the project start command, which replaces the run commands in the README and AGENTS.md, with a development variant using auto-reload for demo mode off only;
  - the single-process design decision and its reason;
  - the settings;
  - PostgreSQL directly, not through a transaction-pooling proxy;
  - `WEB_CONCURRENCY` must be 1, and some platforms set it;
  - the deploy consequence: the new process waits up to the lock wait timeout for the old one to stop, and platforms that wait for the new process to be healthy before stopping the old one need a stop-first strategy;
  - the worst-case startup time;
  - that after the lock is lost, the old process may keep running for up to the heartbeat interval plus the heartbeat timeout plus 2 seconds. That's a known, bounded limit on "exactly one process" (Decisions).

  The detailed reference goes in the architecture document. The README "Running a public demo" section summarizes it.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change, except that the container runs migrations and the server through the project start command. Same migrations, same server, same host and port. All existing tests pass with unchanged outcomes, and the API schema document is identical.
- RNF-2: The lock needs no stored schema. It uses the database's own lock facility, so there is no migration.
- RNF-3: The lock connection doesn't come from the pool used by requests and checks, so their capacity is unchanged.
- RNF-4: Lock behavior is verified on PostgreSQL by `postgres`-marked tests:
  - wait, timeout and one attempt with 0;
  - release on clean stop and on a killed process;
  - a stop signal during the wait;
  - heartbeat failure leading to exit, simulated by ending the lock connection from the database side;
  - the dead-connection probing applied to the lock connection.

  The default SQLite suite tests RF-2 to RF-4 and the start command with a stand-in for the lock.
- RNF-5: The container changes (RF-9) and the change of start command need owner approval in the plan (AGENTS.md: Docker config is ask-first). The owner approved the direction on 2026-11-24.
- RNF-6: Time-based behavior (the wait, the retry cadence, the heartbeat) is tested with an injectable clock or short configured values, never with the defaults.
- RNF-7: The spec 000 to 003a quality gates stay green.

## Edge cases
- An overlapping deploy: the new process waits up to the lock wait timeout. If the platform stops the old one within that time, the new one takes over; if not, the new one refuses. Migrations run only once the new process holds the lock, so a waiting or refused process never migrates.
- `docker stop` while a process waits for the lock: it exits within one second (RF-5).
- The database is down at startup: the start command keeps trying until the lock wait timeout, then refuses with the "could not reach" error. The platform restarts it per its own policy.
- A single heartbeat fails because of a brief network blip: the process exits and restarts, taking the lock again. Accepted (simple and safe). The cost is a restart: seeding, and a reset only if one is due.
- A refusal at startup (for example `WEB_CONCURRENCY` above 1) with a restart policy: a fast restart loop, slowed only by the platform's backoff. Documented.
- The database restarts or fails over: the heartbeat fails, and the process exits and restarts (RF-7).
- Staging and production share one database: they must use different lock names, or one refuses. Documented.
- A transaction-pooling proxy in front of PostgreSQL: the lock can't be held reliably. Unsupported, documented, not detected.
- Development with auto-reload and demo mode on: refused (RF-2). Auto-reload is for demo mode off.

## Out of scope
- Several processes or instances in demo mode, or electing a leader.
- Databases other than PostgreSQL in demo mode.
- Platform restart policies and backoff.

## Completion criteria
- The container starts through the project start command as its main process, with demo mode on and off. With demo mode off, migrations and the server behave as before.
- A second process with demo mode on against the same database waits, then refuses. It starts once the first stops. A killed first process doesn't block it, and a stop signal during the wait ends it within a second.
- Migrations run only after the lock is taken.
- Ending the holder's lock connection makes it exit within the heartbeat bound, and a restart retakes the lock.
- `WEB_CONCURRENCY` above 1, a non-PostgreSQL database, and a start through another command each refuse with the documented message.
- The docs are updated per RF-10. The quality gates are green in CI.

## Decisions made in revision
Earlier decisions I1 to I8 still apply unless refined here. Items marked "owner" came from your answers ("go with your recommendations", 2026-11-24).

- **M1 (owner, decision 1) – the start command moves here from 003b.** It validates settings, checks `WEB_CONCURRENCY`, takes the lock with proper signal handling, runs migrations only after the lock, then starts the server in the same process (RF-1 to RF-5).
  - It fixes the verified problem that a lock wait inside application startup can't honor a stop signal (finding 1.1).
  - It also fixes migrations running before the lock (3.2).
  - 003b now only adds its server options for demo mode.
- **M2 (owner, decision 2) – Docker changes:** the container starts the command directly as its main process (3.3) with a long enough healthcheck start period (RF-9, 2.4). Approved in direction, with details in the plan (RNF-5).
- **M3 (owner, decision 4) – lock loss:**
  - the heartbeat has its own timeout, which also bounds the connect (1.1, 2.1);
  - the first failed heartbeat stops the demo jobs at once and exits (RF-7, 3.5);
  - the bounded overlap after a loss is documented as a known limit (2.3);
  - exiting rolls back a running reset, the one interruption 003c allows (2.2).
- **M4 (1.2) – database-side detection:** the lock connection asks the database to probe it, so the database itself releases the lock on a dead client (RF-6). "About" is defined by that one setting. Unix sockets are covered by process death.
- **M5 (1.3) – lock identity and cadence:** the lock is identified by a lock-name setting plus the database (staging and production must differ). It is retried every second, with 0 meaning one attempt.
- **M6 (1.4) – exit bound:** the process ends within the heartbeat timeout plus 2 seconds of the failed heartbeat. Auto-reload is refused in demo mode, so there's no supervisor left without a worker (RF-2).
- **M7 (1.5, 2.4) – independence from 003c:** 003d works and is testable without 003c. The healthcheck change is delivered here, sized for the defaults including 003c's maximum reset time.
- **M8 (1.6, 4.2) – `WEB_CONCURRENCY`:** decimal digits only, read by the start command, documented as the server's variable rather than a demo setting.
- **M9 (3.1) – database unreachable:** retried within the lock wait timeout, then refused with its own error.
- **M10 (3.4) – tests:** the database-side probing and the exit after the lock connection is ended are verified on PostgreSQL.
- **M11 (4.1) – fixed numbers:** the one-second retry cadence and shutdown response, and the 2-second exit margin, are protocol constants. The heartbeat timeout is a setting.
- **M12 (4.3) – principle 6:** exiting on lock loss is a deliberate safety design. A failing check still never stops the API. Only losing the single-process guarantee does.

## Open questions
- (none)
