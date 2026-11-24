# 003a - Demo mode: seeding, protection and caps

Status: draft (revised 2026-11-24 after `/sdd-clarify` pass 2; the original 003 is now split into 003a, 003b and 003c)

## Context and goal
The API is a portfolio piece, so it should be deployable as a public demo that anyone can try without setup. Deployed as it is today, a public instance is open to misuse:
- There is no authentication (roadmap spec 007, api-auth). Any visitor can create, edit and delete every service, including the examples a reviewer is meant to see.
- Nothing limits visitor-created services. The scheduler checks each one forever, so the demo could be used as a free monitoring service, or aimed many times at one third-party site, at the owner's cost.

Goal: an opt-in **demo mode**, switched on by a deployment setting. In demo mode the API:
- creates and maintains a configured set of example services (**seeded services**), protected from visitor changes, whose history comes only from real checks;
- lets visitors try every endpoint, within a global cap, a per-host cap and a minimum check interval.

With demo mode off, the API behaves as today, with one deliberate fix: a duplicate URL never causes a 500, and a PATCH to an existing URL returns 409 (RF-14).

Demo mode is delivered by three specs. All three must be implemented before a public deployment:
- **003a (this spec):** seeding, protection and caps.
- **003c:** the single-process lock, the periodic reset and the reset notice. It depends on 003a.
- **003b:** rate limits, client identification and the per-client cap. It depends on 003a and 003c.

Implementation order: 003a, then 003c, then 003b.

Roadmap: 003a, 003b and 003c are inserted. metrics-retention becomes 004, alerting 005, postgres-test-parity 006 and api-auth 007 (RF-16).

## Users
- **Portfolio reviewer / visitor:** sees example services with real history and tries the write endpoints without breaking the demo for the next person.
- **Owner/operator:** chooses the seeded services and the limits through settings.
- **Third-party site owners:** must not be checked often by services that visitors register on the demo.

## User stories
- As a visitor, I list the services and see the seeded examples with their real uptime and response-time history.
- As a visitor, I create a service, watch its checks arrive, edit it and delete it, and I get a clear error when I hit a demo limit.
- As a visitor, I cannot edit, deactivate or delete a seeded service.
- As the owner, I enable demo mode and list the seeded services in the settings; they are created and kept as configured.

## Definitions
- **Demo mode:** a deployment setting, off by default. Requirements marked "WHILE demo mode is on" apply only then.
- **Seeded service:** a service defined in the seed list setting. It is created and maintained by the system, never by a visitor.
- **Visitor service:** a service created through the API while demo mode is on. It stays a visitor service even if demo mode is later switched off and on again.
- **Ordinary service:** any other service: one that existed before this feature was installed, one created while demo mode was off, or one that was seeded and later removed from the seed list.
- **Service origin:** which of the three kinds above a service is. It is stored with the service and is not part of any API response.
- **Normalized URL / normalized host:** the URL and host after the parsing and normalization the API applies on create (spec 002). The normalized host is lower-case, has no trailing dot and no port, and uses the ASCII (punycode) form of internationalized names. An IP literal is its own host, and `www.example.com` and `example.com` are different hosts.

### Settings
Read once at startup.

| Setting | Default | Valid range |
|---|---|---|
| Demo mode | off | a boolean: `true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off`, case-insensitive |
| Seed list | empty | 1 to 20 definitions while demo mode is on |
| Global visitor-service cap | 20 | 1 to 1,000 |
| Per-host visitor-service cap | 3 | 1 to the global cap |
| Demo minimum check interval | 300 seconds | 10 to 86,400 seconds |

**Seed list format:** a JSON array of objects.
- Required fields: `name` and `url`.
- Optional fields: `expected_status` (default 200) and `check_interval` (default 60), with the same ranges as the API.
- Any other key is invalid.
- The URL must not contain user information (`user:pass@`).
- The demo minimum check interval does not apply to seeded services.

## Functional requirements

### Settings and startup
- RF-1: THE SYSTEM SHALL read the demo mode setting always. IF its value is not a valid boolean, THEN THE SYSTEM SHALL refuse to start with an error naming the setting.
- RF-2: WHILE demo mode is off, THE SYSTEM SHALL not parse or validate the other demo settings (of 003a, 003b and 003c), so a malformed value never affects startup.
- RF-3: WHILE demo mode is on, IF any 003a setting is missing where required, unparsable or outside its valid range, THEN THE SYSTEM SHALL refuse to start with an error naming the setting.
- RF-4: WHILE demo mode is on, THE SYSTEM SHALL validate every seed definition as in Settings and with spec 002 URL safety, resolving all seed hostnames concurrently so the whole check takes about one resolution timeout. IF a definition is invalid, contains user information, is rejected by spec 002 (including a hostname that resolves to a non-public address), or has the same normalized URL as another definition, THEN THE SYSTEM SHALL refuse to start with an error naming the definition by position and name. Errors SHALL never include user information or resolved addresses. A hostname that cannot be resolved is accepted, as in spec 002 RF-4.

### Seeded services
- RF-5: WHILE demo mode is on, WHEN the system starts and WHEN spec 003c runs a reset, THE SYSTEM SHALL ensure each seeded service exists exactly once, matched by normalized URL, with the seed list's name, expected status and check interval, active, with origin "seeded", and scheduled exactly once.
  - A missing seeded service is created.
  - A service whose normalized URL matches a seed definition becomes seeded, whatever its origin was. Its fields are corrected and its stored checks are kept.
  - The schedule of a seeded service is replaced only if its check interval changed or it has no schedule, so its next check is not postponed.
- RF-6: WHILE demo mode is on, WHEN the system starts, THE SYSTEM SHALL change every service with origin "seeded" whose normalized URL is not in the seed list to origin "ordinary".
- RF-7: IF seeding at startup (RF-5, RF-6) fails, THEN THE SYSTEM SHALL refuse to start with an error. It SHALL never serve requests half-seeded.
- RF-8: THE SYSTEM SHALL NOT generate, backfill, alter or delete metrics of seeded services. Their history comes only from real checks (constitution principle 1).

### Protection
- RF-9: WHILE demo mode is on, WHEN a request updates (including with an empty body) or deletes a seeded service, THE SYSTEM SHALL reject it with HTTP 403 and `{"detail": "Seeded demo services cannot be changed."}`, and SHALL not change the service or its schedule.
- RF-10: WHILE demo mode is on, WHEN a request updates (including with an empty body) or deletes an ordinary service, THE SYSTEM SHALL reject it with HTTP 403 and `{"detail": "This service cannot be changed while demo mode is on."}`.

### Visitor services and caps
- RF-11: WHILE demo mode is on, WHEN a request creates a service, THE SYSTEM SHALL store it with origin "visitor". Visitor services can be updated and deleted by any request, as today.
- RF-12: WHILE demo mode is on, IF a create request, or an update of a visitor service whose body contains `check_interval`, sets a check interval below the demo minimum check interval, THEN THE SYSTEM SHALL reject it with HTTP 422 in the existing request-validation error format. The error is reported on its own, with:
  - location `["body", "check_interval"]`;
  - type `demo_interval_too_low`;
  - message `In demo mode the check interval must be at least <M> seconds`.
- RF-13: WHILE demo mode is on, THE SYSTEM SHALL reject with HTTP 409:
  - a create request when the number of visitor services is already at or above the global cap, with `{"detail": "Demo limit reached: at most <G> visitor services at a time."}`;
  - a create request, or an update that changes a visitor service's normalized host, when the number of other visitor services with that normalized host is already at or above the per-host cap, with `{"detail": "Demo limit reached: at most <H> visitor services for host <host>."}`, where `<host>` is the normalized host.

  The caps SHALL hold under concurrent requests: no sequence or concurrency of requests ever adds a visitor service beyond a cap. Visitor services that already exceed a cap after the owner lowers it stay until deleted or reset.
- RF-14: THE SYSTEM SHALL answer HTTP 409 with `{"detail": "A service with this URL already exists"}` whenever a create or update would give two services the same normalized URL, whether demo mode is on or off. That includes concurrent requests: no request ever returns 500 because of a duplicate URL. This deliberately replaces today's 500 on update, and closes the gap recorded during spec 002.
- RF-15: WHEN a request is rejected for more than one reason, THE SYSTEM SHALL answer with the first that applies, in this order:
  - **Create:** 422 request validation (including spec 002's literal check and RF-12), then 409 global cap, then 409 per-client cap (spec 003b), then 409 per-host cap, then 422 spec 002 hostname check, then 409 duplicate URL.
  - **Update:** 422 request validation (including spec 002's literal check), then 404, then 403 (RF-9, RF-10), then 422 demo minimum interval (RF-12), then 409 per-host cap, then 422 spec 002 hostname check, then 409 duplicate URL.
  - **Delete:** 404, then 403 (RF-9, RF-10).

  The demo caps come before the hostname check, so a request that would be refused anyway causes no DNS lookup. With demo mode off, the order is today's (spec 002) plus RF-14. Spec 003b adds its 429 responses ahead of everything. Spec 003c may delay a write during a reset.

### Switching off and documentation
- RF-16: WHILE demo mode is off, THE SYSTEM SHALL not seed, cap or protect anything, and every service SHALL behave as an ordinary service. Service origins are kept.
- RF-17: THE SYSTEM SHALL document:
  - demo mode and its settings, with defaults and ranges, and the seed list format;
  - the error responses and their order;
  - the RF-14 change;
  - that a public deployment also needs specs 003b and 003c.

  The detailed reference goes in the architecture document. The product SPEC summarizes it and links there. The README has a "Running a public demo" section. The product SPEC roadmap SHALL show 003a, 003b, 003c, the implementation order and the new numbering.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change, except:
  - RF-14;
  - an invalid demo mode value refusing startup (RF-1);
  - the stored service origin.

  All existing tests pass with unchanged outcomes, and the API schema document is identical to the one before this spec.
- RNF-2: The service origin is a stored schema change made through a versioned migration (constitution principle 5). The plan needs owner approval for it. Existing services get origin "ordinary". The spec 001 migration check stays green.
- RNF-3: Behavior that depends on the database engine is verified on PostgreSQL by `postgres`-marked tests: RF-13 and RF-14 under concurrency (including the unique-constraint path) and the migration.
- RNF-4: Schedule requirements (RF-5) are verified with a real scheduler instance, not a mock.
- RNF-5: The spec 000 to 002 quality gates stay green.
- RNF-6: Automated tests cover, at minimum:
  - RF-1 to RF-4 (an invalid flag; malformed demo settings ignored while off; an empty seed list; an unknown key; user information; a duplicate normalized URL; an unsafe seed; error texts without credentials);
  - seeding (created, corrected, adopted from visitor and ordinary, no duplicate, history kept, schedule not postponed);
  - RF-6 demotion and RF-7 refusal;
  - 403 on update (empty and non-empty), deactivate and delete, for seeded and ordinary services;
  - each cap at and above its boundary, including concurrent creates, a lowered cap, and host normalization (case, trailing dot, port, punycode);
  - RF-12 on create and update, present and absent;
  - RF-14 with demo mode on and off, including concurrent duplicates;
  - the RF-15 order for create, update and delete;
  - demo mode off changing nothing except RNF-1's exceptions.

## Edge cases
- A seeded service's target is down: it shows DOWN with real failed checks. Nothing is faked (RF-8).
- The owner changes a seed definition: it takes effect at the next startup, because settings are read once.
- The owner removes a seed definition: at the next startup the service becomes ordinary (RF-6), so it is protected while demo mode is on and is never reset. To delete it, the owner switches demo mode off, deletes it through the API, and switches demo mode back on. No database console is needed (constitution principle 5).
- A transient DNS answer makes a seed hostname resolve to a non-public address at startup: startup is refused (RF-4). The operator restarts once DNS is correct.
- A visitor creates or updates a service to a seeded service's URL: 409 duplicate (RF-14).
- A visitor edits or deletes another visitor's service: allowed. There are no accounts.
- A visitor service is deactivated: it still counts toward the caps.
- The owner raises the demo minimum interval: existing visitor services keep their interval until they are updated or reset.
- Visitor services aimed at the demo's own public address: allowed by spec 002, limited by the per-host cap.
- A fresh deployment: the status endpoint omits seeded services until their first check (existing rule). Each first check comes within one check interval of startup.
- A seeded service's expected status changes: checks stored before the change keep their stored result.

## Out of scope
- The single-process lock, the reset and the notice (spec 003c). Rate limits, client identification and the per-client cap (spec 003b).
- Authentication, accounts or ownership (roadmap 007).
- Synthetic history for seeded services, and retention (roadmap 004).
- Showing a service's origin in API responses.

## Completion criteria
- With demo mode on and a seed list configured, a fresh start creates exactly the seeded services, scheduled and checking. A restart keeps them once each, with their history intact.
- Every 403, 409 and 422 case and the RF-15 order behave as specified in automated tests and in a manual run against the running stack.
- No duplicate URL produces a 500, sequentially or concurrently, with demo mode on and off.
- With demo mode off, all existing tests pass unchanged and the API schema document is identical.
- The docs and roadmap are updated per RF-17. The quality gates are green in CI.

## Decisions made in revision
Pass 1 decisions D1 to D27 of the original 003 still apply where their topic stayed in this spec. Below are this pass's decisions; items marked "owner" came from your answers.

- **E1 (owner) – split:** 003a = seeding, protection and caps. 003c = the single-process lock, reset and notice. 003b is unchanged in scope.
  - Implementation order: 003a, 003c, 003b, because 003b uses 003c's reset hook and single process.
- **E2 (2.2) – settings validation:** each spec validates its own settings while demo mode is on.
  - The demo flag is always validated (RF-1).
  - Other demo settings are not parsed while demo mode is off (RF-2). This confirms the eager-parsing risk, which was reproduced in pydantic-settings.
- **E3 (1.6, 4.4) – seed list format:** required and optional fields with API defaults; unknown keys and user information rejected; errors never echo credentials.
- **E4 (3.7) – startup DNS:** seed hostnames are resolved concurrently, about one resolution timeout in total, which stays inside the container's 15 s healthcheck start period. A transient non-public answer refuses startup.
- **E5 (1.7) – protection:** an empty PATCH on a protected service is also a 403. RF-12 applies only when `check_interval` is in the body, and it is reported on its own.
- **E6 (1.10) – normalized host:** defined (lower-case, no trailing dot, no port, punycode). The cap message shows the normalized host.
- **E7 (3.2, 3.3) – changed limits:** caps refuse at "at or above". Excess left after the owner lowers a cap, or old intervals after the minimum is raised, stay until deleted, updated or reset.
- **E8 (3.5) – duplicates:** no duplicate URL ever returns 500, including concurrent requests on create and update (RF-14), verified on PostgreSQL.
- **E9 (2.4) – DNS order:** on create, the demo caps come before the hostname check, so refused requests cause no DNS lookup. The duplicate 409 stays after the hostname 422, as spec 002 requires.
- **E10 (2.7) – schedule tests:** verified with a real scheduler instance (RNF-4).
- **E11 (1.3, 3.8) – startup seeding:** happens before any reset (spec 003c orders startup), so adoption keeps history. A seeding failure refuses startup (RF-7).
- **E12 (2.6) – format:** a status line with a short revision note and a decisions section are kept, as in specs 002 and the 003 drafts, because they make review easier.

## Open questions
- (none)
