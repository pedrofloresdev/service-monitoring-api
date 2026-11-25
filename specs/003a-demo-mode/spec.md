# 003a - Demo mode: seeding, protection and caps

Status: approved (2026-11-24, after five `/sdd-clarify` passes and a confirmation pass)

## Context and goal
The API is a portfolio piece, so it should be deployable as a public demo that anyone can try without setup. Deployed as it is today, a public instance is open to misuse:
- There is no authentication (roadmap spec 007, api-auth). Any visitor can create, edit and delete every service, including the examples a reviewer is meant to see.
- Nothing limits visitor-created services. The scheduler checks each one forever, so the demo could be used as a free monitoring service, or aimed many times at one third-party site, at the owner's cost.

Goal: an opt-in **demo mode**, switched on by a deployment setting. In demo mode the API:
- creates and maintains a configured set of example services (**seeded services**), protected from visitor changes, whose history comes only from real checks;
- lets visitors try every endpoint, within a global cap, a per-host cap and a minimum check interval.

With demo mode off, the API behaves as today, with two deliberate fixes: a duplicate URL never causes a 500 (RF-14), and a PATCH that sets a required field to `null` gets a 422 instead of a 500 (RF-19). RNF-1 lists every change.

Demo mode is delivered by four specs. All four must be implemented before a public deployment:
- **003a (this spec):** seeding, protection and caps.
- **003d:** the project start command and the single-process lock.
- **003c:** the periodic reset and the reset notice. It depends on 003a and 003d.
- **003b:** rate limits, client identification and the per-client cap. It depends on 003a, 003c and 003d.

Implementation order: 003a, then 003d, then 003c, then 003b.

Roadmap: 003a to 003d are inserted. metrics-retention becomes 004, alerting 005, postgres-test-parity 006 and api-auth 007 (RF-17).

## Users
- **Portfolio reviewer / visitor:** sees example services with real history and tries the write endpoints without breaking the demo for the next person.
- **Owner/operator:** chooses the seeded services and the limits through settings.
- **Third-party site owners:** must not be checked often by services that visitors register on the demo.

## User stories
- As a visitor, I list the services and see the seeded examples with their real uptime and response-time history.
- As a visitor, I create a service, without having to choose an interval, watch its checks arrive, edit it and delete it, and I get a clear error when I hit a demo limit.
- As a visitor, I cannot edit, deactivate or delete a seeded service.
- As the owner, I enable demo mode and list the seeded services in the settings; they are created and kept as configured.

## Definitions
- **Demo mode:** a deployment setting, off by default. Requirements marked "WHILE demo mode is on" apply only then.
- **Seeded service:** a service defined in the seed list setting. It is created and maintained by the system, never by a visitor.
- **Visitor service:** a service created through the API while demo mode is on. It stays a visitor service even if demo mode is later switched off and on again.
- **Ordinary service:** any other service: one that existed before this feature was installed, one created while demo mode was off, or one that was seeded and later removed from the seed list.
- **Service origin:** which of the three kinds above a service is. It is stored with the service and is not part of any API response.
- **Normalized URL:** the text the API's URL parser produces for a URL. That is what the API stores today. When matching seeds (RF-5) or checking duplicates (RF-14), stored URLs are parsed again with the same parser, so a row written outside the API in another form (for example `http://Example.com`) still matches.
- **Cap host:** the host used to count services toward the per-host cap. It is derived from a service's stored URL: the host the API's URL parser produces, lower-cased, with any trailing dot removed and internationalized names in their ASCII (punycode) form, without brackets for IPv6 literals and without the port.
  - Stored URLs themselves are not changed.
  - An IP literal is its own cap host, and `www.example.com` and `example.com` are different cap hosts.

### Settings
Read once at startup. Integers use decimal digits only: no sign, no decimal point, no separators, no spaces.

| Setting (environment variable) | Default | Valid values |
|---|---|---|
| Demo mode (`DEMO_MODE`) | off | `true`, `false`, `1`, `0`, `yes`, `no`, `on`, `off`, case-insensitive. Unset means off. An empty or any other value is invalid. |
| Seed list (`DEMO_SEED_SERVICES`) | empty | JSON, 1 to 20 definitions while demo mode is on |
| Global visitor-service cap (`DEMO_GLOBAL_VISITOR_CAP`) | 20 | integer 1 to 1,000 |
| Per-host visitor-service cap (`DEMO_PER_HOST_VISITOR_CAP`) | 3 | integer 1 to the global cap |
| Demo minimum check interval (`DEMO_MIN_CHECK_INTERVAL_SECONDS`) | 300 | integer 10 to 86,400 |

**Seed list format:** a JSON array of objects.
- `name`: string, 1 to 100 characters, required.
- `url`: string, required. It must be a URL the API's own `url` field accepts (`http` or `https`, same length and syntax rules), and must not contain user information (`user:pass@`).
- `expected_status`: JSON integer, 100 to 599, optional. Default: the API's default.
- `check_interval`: JSON integer, 10 to 86,400, optional. Default: the API's default check interval. The demo minimum does not apply to seeded services.
- Any other key, a value of the wrong JSON type (for example a string, a float or `true` for a number), or a top-level value that isn't an array is invalid.
- Duplicate names are allowed.

## Functional requirements

### Settings and startup
- RF-1: THE SYSTEM SHALL read the demo mode setting always. IF it is set to an invalid value, THEN THE SYSTEM SHALL refuse to start with an error naming `DEMO_MODE`.
- RF-2: WHILE demo mode is off, THE SYSTEM SHALL not parse or validate the other demo settings (of 003a to 003d), so a malformed value never affects startup.
- RF-3: WHILE demo mode is on, IF any 003a setting is unparsable or outside its valid values, or the per-host cap is greater than the global cap, THEN THE SYSTEM SHALL refuse to start with an error naming the environment variable (both variables for the cap rule). For the seed list as a whole (not valid JSON, not an array, empty, or more than 20 definitions), the error names `DEMO_SEED_SERVICES` and which of these problems it is. 003d's start command reports this together with the other settings errors.
- RF-4: WHILE demo mode is on, THE SYSTEM SHALL validate every seed definition as in Settings and with spec 002 URL safety, resolving all seed hostnames concurrently so the whole check takes about one resolution timeout. IF a definition is invalid, is rejected by spec 002 (including a hostname that resolves to a non-public address), or has the same normalized URL as another definition, THEN THE SYSTEM SHALL refuse to start with an error naming `DEMO_SEED_SERVICES` and the definition by its 1-based position, plus its name when the name itself is valid. Errors SHALL never include user information or resolved addresses. A hostname that cannot be resolved is accepted, as in spec 002 RF-4.

### Seeded services
- RF-5: WHILE demo mode is on, WHEN the system starts and WHEN spec 003c runs a reset, THE SYSTEM SHALL make these data changes so that each seeded service exists exactly once, matched by normalized URL, with the seed list's name, expected status and check interval, active, and with origin "seeded":
  - A missing seeded service is created.
  - A service whose normalized URL matches a seed definition becomes seeded, whatever its origin was. Its fields are corrected, it is reactivated if it was inactive, and its stored checks are kept.
  - IF several stored services match one seed definition (legacy forms of the same URL), THEN the one with the lowest id is adopted, and the others keep their origin and are logged as a warning.
  - A stored URL that the parser can't read matches nothing, counts toward no cap host, and is logged as a warning. It never causes a startup failure or a 500.

  RF-5 makes these changes inside the caller's transaction and doesn't commit on its own. At startup, the changes (with RF-6) are committed as one transaction. At a reset, they are part of 003c's reset transaction. Schedules follow the data: at startup the scheduler is started afterwards and schedules every active service; at a reset, 003c brings the schedules in line after the commit.
- RF-6: WHILE demo mode is on, WHEN the system starts, THE SYSTEM SHALL change every service with origin "seeded" whose normalized URL is not in the seed list to origin "ordinary".
- RF-7: IF seeding at startup (RF-5, RF-6) fails, THEN THE SYSTEM SHALL refuse to start with an error. It SHALL never serve requests half-seeded.
- RF-8: WHILE demo mode is on, WHEN the scheduler starts, and WHEN a reset creates, adopts or reactivates a seeded service, THE SYSTEM SHALL start the first check of each such seeded service immediately, not one interval later. These first checks are exempt from the scheduler's lateness cutoff: none is skipped, even when they have to wait for free check workers (for example 20 seeds with slow targets and 10 check workers). All other check jobs keep today's behavior (RNF-1). Frequent restarts therefore never starve a seeded service of checks.
- RF-9: THE SYSTEM SHALL NOT generate, backfill or alter metrics of seeded services, and the system SHALL never delete those metrics. Their history comes only from real checks (constitution principle 1). Deleting a service through the API still deletes its checks, as today: the owner's deletes with demo mode off, and visitors' deletes of visitor services with demo mode on. These are deletes requested through the API, not system deletions.

### Protection
- RF-10: WHILE demo mode is on, WHEN a request updates (including with an empty body) or deletes a seeded service, THE SYSTEM SHALL reject it with HTTP 403 and `{"detail": "Seeded demo services cannot be changed."}`, and SHALL not change the service or its schedule.
- RF-11: WHILE demo mode is on, WHEN a request updates (including with an empty body) or deletes an ordinary service, THE SYSTEM SHALL reject it with HTTP 403 and `{"detail": "This service cannot be changed while demo mode is on."}`.

### Visitor services and caps
- RF-12: WHILE demo mode is on, WHEN a request creates a service, THE SYSTEM SHALL store it with origin "visitor". IF the request omits `check_interval`, THEN the service gets the demo minimum check interval instead of the API's default. Visitor services can be updated and deleted by any request, as today.
- RF-13: WHILE demo mode is on, AFTER request validation passes, IF a create request, or an update of a visitor service, contains a `check_interval` below the demo minimum, THEN THE SYSTEM SHALL reject it with HTTP 422. The response has a single entry in the existing request-validation error format:
  - location `["body", "check_interval"]`;
  - type `demo_interval_too_low`;
  - message `In demo mode the check interval must be at least <M> seconds`;
  - `input` set to the submitted value.

  A value outside the API's own range (for example 5) fails request validation first. On an update, this applies even when the value equals the service's current one.
- RF-14: THE SYSTEM SHALL answer HTTP 409 with `{"detail": "A service with this URL already exists"}` whenever a create or update would give two services the same normalized URL, whether demo mode is on or off.
  - Detection has two layers: before storing, the system compares normalized URLs, which catches legacy forms the database's raw-text constraint can't see. When storing, the database's uniqueness constraint catches concurrent races. Both give this 409. Any other database error is not reported as a duplicate.
  - No request ever returns 500 because of a duplicate URL.
  - An update that keeps a service's own URL is not a duplicate.
  - This deliberately replaces today's 500 on update, and closes the gap recorded during spec 002.
- RF-15: WHILE demo mode is on, THE SYSTEM SHALL reject with HTTP 409:
  - a create request when the number of visitor services is already at or above the global cap, with `{"detail": "Demo limit reached: at most <G> visitor services at a time."}`;
  - a create request, or an update that changes a visitor service's cap host, when the number of other visitor services with that cap host is already at or above the per-host cap, with `{"detail": "Demo limit reached: at most <H> visitor services for host <host>."}`, where `<host>` is the cap host.

  The caps are checked before the spec 002 hostname check, and checked again in the same atomic step that stores the change, with no network lookup inside that step. The step is serialized by the database inside the storing transaction, for creates and for updates that change a cap host. So the caps hold even with several processes, before spec 003d's single-process guarantee exists. No sequence or concurrency of requests ever adds a visitor service beyond a cap. Visitor services that already exceed a cap, after the owner lowers it, stay until deleted or reset.
- RF-16: Every rejection in this spec (RF-10 to RF-15, RF-19) SHALL leave the service and its schedule unchanged. WHEN a request is rejected for more than one reason, THE SYSTEM SHALL answer with the first that applies, in this order:
  - **Create:** 422 request validation (including spec 002's literal check), then 422 demo minimum interval (RF-13), then 409 global cap, then 409 per-client cap (spec 003b), then 409 per-host cap, then 422 spec 002 hostname check, then 409 duplicate URL.
  - **Update:** 422 request validation (including spec 002's literal check), then 404, then 403 (RF-10, RF-11), then 422 demo minimum interval (RF-13), then 409 per-host cap, then 422 spec 002 hostname check, then 409 duplicate URL.
  - **Delete:** 422 request validation (for example a non-numeric id), then 404, then 403 (RF-10, RF-11).

  The only exception is concurrency: a cap filled by another request during this request's hostname check gives a 409 cap from the atomic re-check (RF-15). With demo mode off, the order is today's (spec 002) plus RF-14. Spec 003b adds its 429 ahead of everything, and spec 003c may hold a write during a reset before any of these checks.

### Switching off and documentation
- RF-17: THE SYSTEM SHALL document:
  - demo mode and its settings (names, defaults, valid values) and the seed list format;
  - the error responses and their order;
  - the RF-12 default interval and the RF-14 change;
  - that when demo mode is turned on, every existing service that isn't a visitor or seeded service is ordinary and protected (services kept from an earlier demo period keep their origin);
  - that a public deployment needs specs 003b to 003d too.

  The detailed reference goes in the architecture document. The product SPEC summarizes it and links there. The README gets a "Running a public demo" section, which 003b to 003d extend. The product SPEC roadmap SHALL show 003a to 003d, the implementation order and the new numbering.
- RF-18: WHILE demo mode is off, THE SYSTEM SHALL not seed, cap or protect anything, and every service SHALL behave as an ordinary service. Service origins are kept.
- RF-19: WHEN an update request sets `name`, `url`, `expected_status`, `check_interval` or `is_active` to `null`, THE SYSTEM SHALL reject it with HTTP 422 in the existing request-validation error format, with one entry per such field, type `null_not_allowed` and message `<field> cannot be null`. This applies whether demo mode is on or off, and replaces today's 500. It is part of request validation, so it comes first in the RF-16 order.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change, except:
  - RF-14;
  - RF-19;
  - an invalid `DEMO_MODE` refusing startup (RF-1);
  - the stored service origin.

  All existing tests pass with unchanged outcomes, and the API schema document is identical to the one before this spec.
- RNF-2: The service origin is the only stored schema change in this spec. It is made through a versioned migration (constitution principle 5), and the plan needs owner approval for it. Existing services get origin "ordinary". The spec 001 migration check stays green.
- RNF-3: Behavior that depends on the database engine is verified on PostgreSQL by `postgres`-marked tests: RF-14 and RF-15 under concurrency, for create and for an update that changes the host (including the unique-constraint path), and the migration.
- RNF-4: Schedule requirements (RF-5, RF-8) are verified with a real scheduler instance, not a mock.
- RNF-5: The spec 000 to 002 quality gates stay green.
- RNF-6: Automated tests cover, at minimum:
  - RF-1 to RF-4:
    - `DEMO_MODE` values: valid, invalid, empty, unset;
    - malformed demo settings ignored while off;
    - an empty seed list, an unknown key, a wrong JSON type, user information, a duplicate normalized URL and an unsafe seed;
    - error texts without credentials;
  - seeding (created, corrected, adopted from visitor and ordinary, reactivated, no duplicate, history kept);
  - RF-6 demotion, RF-7 refusal and RF-8 immediate first check;
  - 403 on update (empty and non-empty), deactivate and delete, for seeded and ordinary services;
  - RF-12, with and without `check_interval`;
  - RF-13 on create and update (present, absent, equal to current, below the API range);
  - each cap at and above its boundary, a lowered cap, cap host derivation (case, trailing dot, port, punycode, IPv6), and concurrency;
  - RF-14 with demo mode on and off, own URL, concurrent duplicates, a legacy non-canonical stored URL, and a non-URL database error not turned into a duplicate;
  - RF-19 for each field, with demo mode on and off;
  - the per-host > global cap refusal, integer spellings, and a seed URL the API would reject;
  - RF-8 with more seeds than check workers and slow targets, no first check skipped, and other jobs' lateness cutoff unchanged;
  - several stored rows matching one seed, and an unreadable stored URL;
  - each rejection leaving the service and its schedule unchanged;
  - seed list errors for invalid JSON, a non-array, an empty list and more than 20 definitions;
  - the RF-16 order for create, update and delete;
  - demo mode off changing nothing except RNF-1's exceptions.

## Edge cases
- A seeded service's target is down: it shows DOWN with real failed checks. Nothing is faked (RF-9).
- The owner changes a seed definition: it takes effect at the next startup, because settings are read once.
- The owner removes a seed definition: at the next startup the service becomes ordinary (RF-6), so it is protected while demo mode is on and is never reset. To delete it, the owner switches demo mode off, deletes it through the API, and switches demo mode back on.
- Demo mode is turned on for the first time over an existing database: every existing service is ordinary and protected. Ordinary services don't count toward the caps. A seed URL that matches an existing, inactive service adopts and reactivates it.
- A transient DNS answer makes a seed hostname resolve to a non-public address at startup: startup is refused (RF-4). The operator restarts once DNS is correct.
- A visitor creates or updates a service to a seeded service's URL: 409 duplicate (RF-14).
- `http://example.com/` and `http://example.com./` are different normalized URLs, so both may exist. They share one cap host, and the per-host cap limits them together.
- Visitor services on a seeded service's host: allowed up to the per-host cap. Seeded services don't count toward it. Accepted, because the cap is small.
- A visitor edits or deletes another visitor's service: allowed. There are no accounts.
- A visitor service is deactivated: it still counts toward the caps.
- The owner raises the demo minimum interval: existing visitor services keep their interval until they are updated (RF-13 then applies) or reset. The same holds for a visitor service created before demo mode was last switched off.
- Visitor services aimed at the demo's own public address: allowed by spec 002, and limited by the per-host cap.
- Aliases of one target (other hostnames, CNAMEs, subdomains, an IP literal) are separate cap hosts, so only the global cap bounds them together. Accepted and documented.
- A fresh deployment: seeded services get their first check immediately (RF-8), so the status endpoint shows them within seconds.
- A seeded service's expected status changes: checks stored before the change keep their stored result. Status and uptime briefly mix both expectations. No metric is altered (constitution principle 1).
- 003a deployed without 003b to 003d: demo mode works but isn't safe for public use. This is documented (RF-17) and is not enforced.

## Out of scope
- The single-process lock (003d), the reset and notice (003c), rate limits and the per-client cap (003b).
- Authentication, accounts or ownership (roadmap 007).
- Synthetic history for seeded services, and retention (roadmap 004).
- Showing a service's origin in API responses.

## Completion criteria
- With demo mode on and a seed list configured, a fresh start creates exactly the seeded services and checks each of them at once. A restart keeps them once each, with their history intact.
- Every 403, 409 and 422 case and the RF-16 order behave as specified in automated tests and in a manual run against the running stack.
- No duplicate URL produces a 500, sequentially or concurrently, with demo mode on and off.
- With demo mode off, all existing tests pass unchanged and the API schema document is identical.
- The docs and roadmap are updated per RF-17. The quality gates are green in CI.

## Decisions made in revision
Earlier decisions (D1 to D27 of the original 003, E1 to E12 of pass 2) still apply unless refined here. Pass 3 decisions follow; items marked "owner" came from your answers.

- **H1 (owner, decision 6) – default interval:** a create without `check_interval` in demo mode gets the demo minimum (RF-12), so it never fails on a field the client didn't send (finding 1.1).
- **H2 (owner, decision 1) – split:** the lock moved to the new spec 003d. Implementation order: 003a, 003d, 003c, 003b.
- **H3 (2.1, 1.2) – RF-13 placement:** RF-13 is checked after request validation, on create and update alike, and reported as a single entry with `input`. Values outside the API range fail validation first.
- **H4 (1.3) – RF-13 on update:** applies whenever the body contains `check_interval`, even if the value is unchanged. It is unreachable for protected services, because 403 comes first.
- **H5 (1.4, 4.1) – caps and DNS:** caps are checked before the hostname lookup and re-checked in the atomic store step, which has no network lookup inside. Creates are therefore never serialized behind DNS. The re-check is the only way a cap 409 can follow a lookup.
- **H6 (1.5, 1.6, 3.4) – cap host:** derived from the stored URL for counting only, so stored URLs don't change. There is no stored host column, because counting at most 1,000 visitor services is cheap. IPv6 hosts appear without brackets.
- **H7 (1.7, 4.4) – settings:** environment variable names, exact boolean spellings, empty value invalid, and seed field types (JSON integers, `name` up to 100 characters), with defaults taken from the API's defaults rather than restated numbers (finding 4.2).
- **H8 (1.8, 3.2) – first check:** seeded services are checked immediately when the scheduler starts (RF-8). This replaces "not postponed", which meant nothing at startup.
- **H9 (2.3, 1.9) – schedule hand-off:** RF-5 makes data changes only. At startup the scheduler is started afterwards. At a reset, 003c reconciles the schedules after the commit.
- **H10 (2.2) – own URL:** keeping a service's own URL isn't a duplicate. The PATCH `url: null` gap stays out of scope.
- **H11 (2.5) – startup time:** E4's healthcheck claim is withdrawn. The combined worst-case startup time is stated in 003c and 003d.
- **H12 (2.6) – owner deletes:** an API delete with demo mode off still deletes checks. That's an owner action, outside RF-9.
- **H13 (2.7, 3.1, 3.3, 3.5, 3.7) – documented edge cases:** 003a alone isn't safe for public use; existing services become ordinary; trailing-dot URL variants share a cap host; visitor services may share a seeded host; old intervals below a raised minimum stay until updated.
- **H14 (3.6) – concurrency tests:** the update path that changes the host is included (RNF-3).

- **Q1 (owner, decision 5 of pass 4) – `null` on required fields:** a PATCH that sets a required field to `null` returns 422 everywhere, instead of 500 (RF-19, finding 3.1). It includes `url: null`, so that spec 002 gap is closed too.
- **Q2 (1.1) – normalized URL:** defined. Stored rows are parsed again for seed matching and duplicate checks, so rows written outside the API still match. This applies the spec 002 lesson.
- **Q3 (1.2) – cross-setting rule:** per-host cap ≤ global cap refuses startup, naming both variables, as 003b does for its own rule.
- **Q4 (1.3) – transaction boundary:** RF-5 doesn't commit on its own. Startup commits it as one transaction, and a reset includes it in its own transaction.
- **Q5 (1.4, 1.5) – seed URL rules and integers:**
  - a seed URL must pass the API's own `url` rules;
  - errors name `DEMO_SEED_SERVICES` with a 1-based position;
  - integers use decimal digits only;
  - `true` is the wrong type for numbers.
- **Q6 (2.1, 2.2) – fixes:** the Context lists both fixes and points to RNF-1, and Delete starts with 422 for a malformed id.
- **Q7 (3.1) – precise duplicate detection:** only a URL-uniqueness violation becomes the duplicate 409 (RF-14).
- **Q8 (3.2) – first checks:** no seeded first check is skipped when check workers are busy (RF-8).
- **Q9 (3.3) – aliases:** they slip past the per-host cap. Accepted and documented; the global cap still bounds them.
- **Q10 (4.1) – visitor deletes:** stated as API-requested deletes, not system deletions (RF-9).

- **T1 (1.1) – first-check exemption:** applies only to seeded first checks, which skip the lateness cutoff. Other jobs are unchanged, so demo mode off is unaffected. The test uses the scheduler's real worker count (10 today).
- **T2 (1.2) – atomic step:** serialized by the database inside the storing transaction, for creates and host-changing updates, so 003a's guarantee doesn't depend on 003d.
- **T3 (1.3) – duplicate detection:** two layers. Normalized URLs are compared before storing, and the database constraint catches races. Both give 409, and other errors don't.
- **T4 (1.4) – wording:** RF-9's "them" now says "those metrics".
- **T5 (1.5) – whole-list seed errors:** they name `DEMO_SEED_SERVICES` and the problem.
- **T6 (2.1) – ordinary services:** the documentation statement now covers origins kept from earlier demo periods.
- **T7 (3.1) – stored-row ambiguity:** the lowest id is adopted. Unreadable stored URLs are ignored with a warning and never cause a startup failure or a 500.
- **T8 (3.2) – rejections:** every rejection leaves the service and its schedule unchanged.
- **T9 (3.3) – reset-created seeds:** get the immediate first check too.

## Open questions
- (none)
