# 003a - Demo mode

Status: draft (revised 2026-11-23 after `/sdd-clarify` pass 1; split into 003a and 003b)

## Context and goal
The API is a portfolio piece, so it should be deployable as a public demo that anyone can try without setup. Deployed as it is today, a public instance is open to misuse:
- There is no authentication (roadmap spec 007, api-auth). Any visitor can create, edit and delete every service, including the examples a reviewer is meant to see.
- Nothing limits visitor-created services. The scheduler checks each one forever, so the demo could be used as a free monitoring service, or pointed at one third-party site many times, at the owner's cost.
- Nothing returns the demo to a known state. After a few days it shows whatever visitors left behind.

Goal: an opt-in **demo mode**, switched on by a deployment setting. In demo mode the API:
- creates and maintains a configured set of example services (**seeded services**), protected from visitor changes, whose history comes only from real checks;
- lets visitors try every endpoint, within a global cap, a per-host cap and a minimum check interval;
- periodically removes everything visitors created, at a fixed time of day in UTC, and tells visitors when the next reset happens;
- runs as a single process, which it enforces.

With demo mode off, the API behaves as it does today, except for one deliberate fix: a PATCH that sets a URL already used by another service returns 409 instead of 500 (RF-12). This closes a known gap recorded during spec 002.

This spec is part 1 of 2. Spec **003b-rate-limiting** adds per-client rate limits, client identification and the per-client visitor-service cap. Both are needed before a public deployment. 003b depends on this spec.

Roadmap: 003a and 003b are inserted. The later roadmap specs each move down one number: metrics-retention becomes 004, alerting 005, postgres-test-parity 006 and api-auth 007 (RF-22).

## Users
- **Portfolio reviewer / visitor:** opens the public demo, sees example services with real history, and tries the read and write endpoints without breaking the demo for the next person.
- **Owner/operator:** deploys the demo, chooses the seeded services and limits through settings, and doesn't want to clean up after visitors or pay for abuse.
- **Third-party site owners:** must not be checked often by services that visitors register on the demo.

## User stories
- As a visitor, I list the services and see the seeded examples with their real uptime and response-time history.
- As a visitor, I create a service, watch its checks arrive, edit it and delete it, and I get a clear error when I hit a demo limit.
- As a visitor, I cannot edit, deactivate or delete a seeded service, so the next visitor sees the same examples.
- As a visitor, I can see from `GET /` that the demo resets, and when.
- As the owner, I enable demo mode and list the seeded services in the settings; the demo then maintains itself.

## Definitions
- **Demo mode:** a deployment setting, off by default. Requirements marked "WHILE demo mode is on" apply only then.
- **Seeded service:** a service defined in the seed list setting. It is created and maintained by the system, never by a visitor.
- **Visitor service:** a service created through the API while demo mode is on. It stays a visitor service even if demo mode is later switched off and on again.
- **Ordinary service:** any other service: one that existed before this feature was installed, one created while demo mode was off, or one that was seeded and later removed from the seed list.
- **Service origin:** which of the three kinds above a service is. It is stored with the service and does not appear in API responses.
- **Normalized URL / host:** the URL and host after the same parsing and normalization the API applies when a service is created (spec 002).
- **Reset times:** every instant `A + k × I` in UTC, for whole numbers `k`, where `A` is the reset anchor time of day on 1970-01-01 and `I` is the reset interval. With the defaults (00:00 UTC, 24 hours), a reset happens every day at 00:00 UTC.
- **Next reset time:** the earliest reset time that is strictly later than now.

### Demo settings
All demo settings are read once at startup. They are validated only while demo mode is on. While demo mode is off, they are ignored, even if malformed.

| Setting | Default | Valid range |
|---|---|---|
| Demo mode | off | on / off |
| Seed list | none | 1 to 20 definitions; required while demo mode is on |
| Global visitor-service cap | 20 | 1 to 1,000 |
| Per-host visitor-service cap | 3 | 1 to the global cap |
| Demo minimum check interval | 300 seconds | 10 to 86,400 seconds |
| Reset interval | 24 hours | 1 to 168 whole hours |
| Reset anchor time | 00:00 | a UTC time of day, `HH:MM` |

The seed list is a single setting holding a JSON array of objects, each with `name`, `url`, `expected_status` and `check_interval`, using the same field rules as a service created through the API. The demo minimum check interval does not apply to seeded services.

## Functional requirements

### Startup and settings
- RF-1: WHILE demo mode is on, IF any demo setting is missing where required, unparsable, or outside its valid range, THEN THE SYSTEM SHALL refuse to start with an error naming the setting.
- RF-2: WHILE demo mode is on, THE SYSTEM SHALL validate every seed definition with the same rules as a service created through the API, including URL safety (spec 002). IF a definition is invalid, if its host is rejected by spec 002 (including a hostname that resolves to a non-public address), or if two definitions have the same normalized URL, THEN THE SYSTEM SHALL refuse to start with an error naming the definition. A hostname that cannot be resolved is accepted, as in spec 002 RF-4.
- RF-3: WHILE demo mode is on, IF another process with demo mode on is already using the same database, or the configured number of server workers is greater than one, THEN THE SYSTEM SHALL refuse to start with an error saying that demo mode requires a single process. A process that stopped or crashed SHALL never prevent the next start.

### Seeded services
- RF-4: WHILE demo mode is on, WHEN the system starts and WHEN a reset runs, THE SYSTEM SHALL ensure that each seeded service exists exactly once, matched by normalized URL, with the seed list's name, expected status and check interval, active, with origin "seeded", and scheduled exactly once.
  - A missing seeded service is created.
  - A service whose normalized URL matches a seed definition becomes seeded, whatever its origin was, and its fields are corrected.
  - The schedule of a seeded service is replaced only if its check interval changed or it has no schedule, so its next check is not postponed.
- RF-5: WHILE demo mode is on, WHEN the system starts, THE SYSTEM SHALL change every service with origin "seeded" whose URL is no longer in the seed list to origin "ordinary".
- RF-6: THE SYSTEM SHALL NOT generate, backfill, alter or delete metrics of seeded services. Their history comes only from real checks (constitution principle 1).

### Protection
- RF-7: WHILE demo mode is on, WHEN a request updates or deletes a seeded service, THE SYSTEM SHALL reject it with HTTP 403 and `{"detail": "Seeded demo services cannot be changed."}`, and SHALL not change the service or its schedule.
- RF-8: WHILE demo mode is on, WHEN a request updates or deletes an ordinary service, THE SYSTEM SHALL reject it with HTTP 403 and `{"detail": "This service cannot be changed while demo mode is on."}`.

### Visitor services and caps
- RF-9: WHILE demo mode is on, WHEN a request creates a service, THE SYSTEM SHALL store it with origin "visitor". Visitor services can be updated and deleted by any request, as today.
- RF-10: WHILE demo mode is on, IF a create request, or an update of a visitor service, sets a check interval below the demo minimum check interval, THEN THE SYSTEM SHALL reject it with HTTP 422 in the existing request-validation error format. The error entry SHALL have location `["body", "check_interval"]`, type `demo_interval_too_low` and message `In demo mode the check interval must be at least <M> seconds`, where `<M>` is the setting.
- RF-11: WHILE demo mode is on, THE SYSTEM SHALL reject with HTTP 409:
  - a create request when the number of visitor services is already equal to the global cap, with `{"detail": "Demo limit reached: at most <G> visitor services at a time."}`;
  - a create request, or an update of a visitor service's URL, when the number of other visitor services with the same normalized host is already equal to the per-host cap, with `{"detail": "Demo limit reached: at most <H> visitor services for host <host>."}`, where `<host>` is the normalized host the client sent.

  The caps SHALL hold under concurrent requests: no sequence or concurrency of requests ever leaves more visitor services than a cap allows.
- RF-12: WHEN a request updates a service's URL to the normalized URL of another existing service, THE SYSTEM SHALL reject it with HTTP 409 and `{"detail": "A service with this URL already exists"}` (the create message), whether demo mode is on or off. This deliberately replaces today's 500 response and closes the gap recorded during spec 002.
- RF-13: WHEN a request is rejected for more than one reason, THE SYSTEM SHALL answer with the first that applies, in this order:
  - **Create:** 422 request validation (including spec 002's literal check and, in demo mode, RF-10), then 422 spec 002 hostname check, then 409 global cap, then 409 per-host cap, then 409 duplicate URL.
  - **Update:** 422 request validation (including spec 002's literal check), then 404, then 403 (RF-7, RF-8), then 422 demo minimum interval (RF-10), then 422 spec 002 hostname check, then 409 per-host cap, then 409 duplicate URL.
  - **Delete:** 404, then 403 (RF-7, RF-8).

  Spec 003b adds its 429 responses ahead of all of these.

### Periodic reset
- RF-14: WHILE demo mode is on, THE SYSTEM SHALL run a reset at each reset time. WHEN the system starts and the last completed reset is earlier than the most recent past reset time (or no reset has ever completed), THE SYSTEM SHALL run one reset at startup to catch up. Otherwise it SHALL NOT reset at startup, so restarts and deploys do not delete visitor data.
- RF-15: WHEN a reset runs, THE SYSTEM SHALL delete every visitor service and its stored checks, remove their schedules, apply RF-4, and record the time the reset completed. Seeded and ordinary services and their stored checks SHALL be kept. Spec 003b clears its per-client attribution at this point.
- RF-16: WHEN a reset runs, THE SYSTEM SHALL complete it as one unit:
  - the data changes are committed together or not at all;
  - write requests are not applied in the middle of a reset;
  - afterwards, every active service in the database has exactly one schedule, and no schedule exists for a deleted or inactive service.
- RF-17: WHEN a check of a visitor service finishes after a reset deleted that service, THE SYSTEM SHALL store nothing for it, and SHALL never attach its result to another service.
- RF-18: IF a reset fails, THEN THE SYSTEM SHALL log the failure, leave the database as it was before that reset, restore the schedules so they match the database (RF-16), keep serving requests and checking services, and try again at the next reset time.
- RF-19: THE SYSTEM SHALL run each reset at its reset time even when all check workers are busy. A reset delayed by a stopped process is caught up by RF-14.

### Demo notice
- RF-20: WHILE demo mode is on, THE SYSTEM SHALL add two fields to the API info response (`GET /`), from the first request after startup:
  - `demo_notice`: the text `Demo data resets every N hours.`, where N is the reset interval (`Demo data resets every 1 hour.` when N is 1);
  - `next_reset_at`: the next reset time, in UTC, as an ISO 8601 timestamp with seconds precision ending in `Z`, for example `2026-11-24T00:00:00Z`.

  The existing fields stay unchanged. While demo mode is off, neither field is present.

### Switching off and documentation
- RF-21: WHILE demo mode is off, THE SYSTEM SHALL not seed, cap, protect or reset anything, and every service SHALL behave as an ordinary service. Service origins are kept, so switching demo mode on again treats former visitor services as visitor services (deleted at the next reset).
- RF-22: THE SYSTEM SHALL document demo mode, the settings table with defaults and ranges, the seed list format, the error responses and their order, the reset schedule and catch-up, the demo notice, the single-process design decision (RF-3), the deliberate PATCH 409 change (RF-12), and the owner-approved exception to constitution principle 1. The detailed reference goes in the architecture document. The product SPEC summarizes it and links to that section. The README has a "Running a public demo" section. The product SPEC roadmap SHALL show 003a, 003b and the new numbering.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change, except for RF-12 and the stored service origin. All existing tests pass with unchanged outcomes, except any test that asserted the old 500 response (there is none today). The API schema document is identical to the one before this spec.
- RNF-2: Every stored schema change for this feature (service origin, last completed reset time) goes through a versioned migration (constitution principle 5). The plan needs owner approval for it. The spec 001 migration check stays green. Existing services get origin "ordinary".
- RNF-3: Single-process design. Demo mode supports exactly one running process. This is a deliberate design decision, documented with its reason: the reset schedule, the in-memory state of spec 003b and the scheduler all assume one process. RF-3 enforces it.
- RNF-4: A reset never blocks read requests. A write request that arrives during a reset waits at most until the reset completes.
- RNF-5: **Benchmark** (measured and recorded in the validation notes; not a CI gate): a reset at maximum size (the global cap of visitor services, each with one day of checks at the demo minimum interval) completes in under 2 seconds on the containerized PostgreSQL.
- RNF-6: Behavior that depends on the database engine is verified on PostgreSQL by the `postgres`-marked tests: RF-15 to RF-17 (deletion, foreign keys, a late check storing nothing), RF-11 under concurrency, and the migration.
- RNF-7: Time-based behavior (reset times, catch-up, `next_reset_at`) is tested with an injectable clock, never by waiting.
- RNF-8: The spec 000 to 002 quality gates stay green: lint, format, strict type check, tests with the coverage floor, the migration check and the PostgreSQL tests.
- RNF-9: Automated tests cover, at minimum:
  - RF-1 to RF-3 refusals, including an empty seed list, a duplicate normalized URL, an unsafe seed, and a stale lock from a crashed process not blocking startup;
  - seeding: created, corrected, adopted from a visitor or ordinary service, no duplicate, history kept, schedule not postponed;
  - RF-5 demotion of a removed seed;
  - 403 on update, deactivate and delete for seeded and ordinary services;
  - each cap at its exact boundary, including concurrent creates;
  - RF-10 on create and update, and RF-12 with demo mode on and off;
  - the RF-13 order for create, update and delete;
  - RF-14 catch-up and no reset on a normal restart;
  - reset contents, atomicity, schedule consistency after success and after failure, and a late check storing nothing;
  - RF-20 fields: the notice for N = 1 and N > 1, `next_reset_at` across an anchor boundary, and both fields absent with demo mode off;
  - demo mode off changing nothing except RF-12.

## Owner-approved exception to constitution principle 1
Recorded 2026-11-23, owner-approved. This is a standing exception to constitution principle 1 ("never ... drop ... a metric"), limited to demo mode:
- A reset deletes every visitor service together with all of its stored checks (RF-15).
- A check of a visitor service that finishes after the reset deleted the service is discarded (RF-17).

Seeded services' checks are never deleted, altered or fabricated (RF-6). Ordinary services' checks are never touched by a reset. Outside demo mode, the exception does not apply.

## Edge cases
- A seeded service's target is down: it shows DOWN with real failed checks. Nothing is faked to make the demo look healthy (RF-6).
- The owner changes a seed definition: the change takes effect at the next startup, because settings are read once (RF-4). Resets keep applying the definitions loaded at startup.
- The owner removes a seed definition: at the next startup the service becomes ordinary (RF-5), so it is protected while demo mode is on and never reset. To delete it, the owner switches demo mode off, deletes it through the API, and switches demo mode back on. No database console is needed (constitution principle 5).
- A visitor creates a service with a seeded service's URL: 409 duplicate (RF-13 order).
- A visitor updates a visitor service's URL to a seeded service's URL: 409 duplicate (RF-12). It never reaches the database error that caused the 500.
- A visitor edits or deletes another visitor's service: allowed. There are no accounts (Out of scope).
- A visitor service is deactivated: it still counts toward the caps until it is deleted or reset.
- Visitor services point at the demo's own public address: allowed by spec 002, and limited by the per-host cap.
- A fresh deployment: the seeded services have no checks yet, so the status endpoint omits them until their first check (existing rule). Each first check comes within one check interval of startup.
- A seeded service's expected status changes: checks stored before the change were judged against the old value and stay as they are (RF-6). A check already running when RF-4 applies the change is judged against the value it started with.
- A read request that overlaps a reset may see some data from before and some from after. Only that request is affected, and the next request sees the result. Accepted (Decisions made in revision).
- The process is stopped across one or more reset times: one catch-up reset runs at the next startup (RF-14), not one per missed time.
- Demo mode is switched off: all services behave as ordinary services and keep being checked. Their origins are kept (RF-21).

## Out of scope
- Rate limiting, client identification and the per-client cap (spec 003b).
- Authentication, accounts or per-visitor ownership (roadmap 007, api-auth).
- Synthetic or backfilled history for seeded services.
- Retention or cleanup of seeded services' history (roadmap 004, metrics-retention).
- Running more than one process or instance in demo mode.
- Showing a service's origin in API responses, or separating seeded and visitor services in the status endpoint.
- Hosting configuration for any specific platform, and a UI.

## Completion criteria
- With demo mode on and a seed list configured, a fresh deployment starts with exactly the seeded services, scheduled and checking. A normal restart keeps visitor services, and the next reset time removes them. Seeded services survive both with their history intact.
- Every 403, 409 and 422 case and the RF-13 order behave as specified in automated tests and in a manual run against the running stack.
- RF-12 returns 409, not 500, for a duplicate URL on update, with demo mode on and off.
- A reset leaves only seeded and ordinary services, with exactly one schedule per active service. A forced reset failure leaves the database and schedules as before.
- A second process started with demo mode on against the same database refuses to start.
- `GET /` shows the notice and the correct `next_reset_at` in demo mode, and is unchanged with demo mode off.
- The RNF-5 benchmark is measured and recorded. The docs and roadmap are updated per RF-22. The quality gates are green in CI.

## Decisions made in revision
Defaults applied after clarify pass 1. Items marked "owner" came from your answers; the others are mine, for you to review.

- **D1 (owner) – split:** this spec covers seeding, protection, caps, reset and the notice. Spec 003b covers rate limiting and client identification.
  - **Deviation, with reason:** the per-client visitor-service cap moved to 003b. It needs client identification, which 003b defines; keeping it here would make 003a depend on 003b. 003a keeps the global cap and the per-host cap. The 3 defaults are unchanged.
- **D2 (owner) – duplicate URL on update:** returns 409 everywhere (RF-12), recorded as a deliberate behavior change that closes the spec 002 gap.
- **D3 (owner) – principle 1:** standing exception for reset deletions (section above).
- **D4 (owner) – single process:** RF-3 and RNF-3. Startup refuses if another demo process uses the same database, which works across hosts, or if a worker count above 1 is configured. Stale locks never block a start.
- **D5 (owner) – defaults:** global cap 20, per-host cap 3, minimum interval 300 s, reset every 24 h anchored at 00:00 UTC, `next_reset_at` available from the first request. Benchmarks are documented, not CI gates (RNF-5).
- **D6 (1.1) – settings table:** defaults and valid ranges are fixed in this spec, and the seed list holds 1 to 20 definitions. The hop count is no longer a demo setting here (it moved to 003b), which resolves 2.1.
- **D7 (1.4) – reset schedule:** reset times follow a fixed formula (Definitions). There is no reset on a normal restart, only a single catch-up reset when one was missed (RF-14). This replaces the earlier "reset at every startup", so restarts and deploys don't wipe visitors (3.7).
- **D8 (1.5, 2.3) – service origin:** a stored marker with three values: seeded, visitor, ordinary. Existing rows become ordinary. Visitor services keep their origin across demo off and on, so they are still reset later.
- **D9 (1.6) – seed matching:** by normalized URL. A matching visitor or ordinary service is adopted as seeded and keeps its real history (RF-4).
- **D10 (1.7) – startup validation:** an unsafe seed refuses startup. An unresolvable seed is accepted, as in spec 002. Seeds are not re-resolved at reset; spec 002 still validates every check.
- **D11 (2.4) – settings change only at startup:** stated in RF-4 and the edge cases.
- **D12 (2.2, 3.16) – error order:** the order is now per method (RF-13). The interval check on update runs after the service lookup, and the hostname DNS check runs after 403, so a request that will be refused doesn't cause a DNS lookup.
- **D13 (3.1, 3.3) – schedule consistency:** an invariant checked after every reset, successful or not: every active service has exactly one schedule, and no orphans exist (RF-16, RF-18). Write requests are not applied mid-reset.
- **D14 (3.2) – concurrent creates:** the caps must hold under concurrency (RF-11), with tests for it (RNF-9).
- **D15 (3.4) – PostgreSQL tests:** deletion, foreign key and late-check behavior are verified on PostgreSQL (RNF-6).
- **D16 (3.5) – reset scheduling:** a reset never waits behind busy check workers (RF-19). The load stays small: at most 20 visitor services checked at most every 300 s.
- **D17 (3.6) – seeded schedules:** not re-created at reset unless their interval changed, so their checks aren't postponed (RF-4).
- **D18 (3.8, 1.8) – reads during a reset:** not blocked. One request overlapping a reset may see mixed data; this is accepted and documented. Writes wait for the reset (RNF-4). The circular "single transaction" wording is removed.
- **D19 (1.11, 3.10) – per-host cap:** added (owner default 3) to stop the demo being aimed at one site. Self-pointing services are allowed but count toward it.
- **D20 (3.11) – seed list format:** a JSON array in one setting. An empty list refuses startup. Seeds use the API field rules but not the demo minimum interval. Malformed demo settings are ignored while demo mode is off (RNF-1).
- **D21 (1.12) – exact messages:** every 403, 409 and 422 text is fixed (RF-7, RF-8, RF-10 to RF-12), in the existing error formats.
- **D22 (3.12, 3.13, 3.14) – accepted:** visitor services appear in the status endpoint; a fresh deployment shows no status for seeds until their first check; an expected-status change keeps old checks as they are. All three are documented in the edge cases.
- **D23 (3.15, RNF-7) – time:** reset times are computed from a UTC clock that tests can inject. `next_reset_at` uses seconds precision with `Z`.
- **D24 (4.3) – principle 6:** reads are never blocked, writes wait at most one reset, and the benchmark target is 2 s. The load is small (D16).
- **D25 (4.5) – removing an old seed:** done through the API, with demo mode briefly off. No database console.
- **D26 (2.6) – stale roadmap text:** implemented specs are historical records and stay as they are. The roadmap in the product SPEC is the authority (RF-22).
- **D27 (2.7) – implementation wording:** "single database transaction" is removed. "Versioned migration" and the `X-Forwarded-For` header (in 003b) stay, because they are interface or constitution terms.

## Open questions
- (none)
