# 003b - Demo mode: rate limiting and client identification

Status: draft (revised 2026-11-24 after `/sdd-clarify` pass 5)

## Context and goal
Specs 003a, 003d and 003c make the API deployable as a public demo: seeded services, protection, a global cap and a per-host cap, a single enforced process, and a periodic reset. They don't limit what one client can do:
- One client can send requests as fast as it likes and flood the API or the database.
- One client can create visitor services up to the global cap and keep every other visitor out until the next reset.

Goal: while demo mode is on, every client gets a request budget, with a smaller budget for writes, and its own cap on visitor services. Clients are identified reliably behind a hosting proxy, and a forged header can't escape the limits. While demo mode is off, nothing in this spec applies.

This spec depends on 003a (demo mode, caps, error order), 003d (the project start command and the single process) and 003c (the reset hook and the write gate). Implementation order: 003a, then 003d, then 003c, then 003b.

## Users
- **Visitor:** can explore the demo at a normal pace, and gets a clear 429 with a wait time if it goes too fast.
- **Owner/operator:** is protected from one client flooding the API or taking all visitor-service slots, and configures the limits and the proxy setup.

## User stories
- As a visitor, browsing at a normal pace, including opening the interactive docs, never hits a limit. Too many requests give a 429 that says how long to wait.
- As a visitor, I can create a few services of my own, even when another visitor has been busy.
- As the owner, behind my host's proxy each visitor is counted separately, and a client cannot escape its limits by forging a header.

## Definitions
- **Liveness request:** an HTTP request whose path is exactly `/health`, with any method (a POST to `/health` is liveness too, not a write) and any query string. `/health/` and `/Health` are not liveness requests.
- **Write request:** an HTTP request with method POST, PUT, PATCH or DELETE, on any path, including unknown paths. It is classified by method alone, before any processing.
- **Counted request:** every HTTP request that is not a liveness request. That includes `GET /`, the interactive docs and API schema document, unknown paths, and `HEAD` and `OPTIONS` requests. Non-HTTP connections (the app serves none) are not counted.
- **Connection address:** the IP address of the network peer as the application receives it, with any IPv6 zone ID removed. 003d's project start command, with this spec's server options (RF-2), guarantees that the server never replaces it from forwarding headers.
- **Client:** the key a request is counted under, derived from the client address (RF-6):
  - an IPv4 address is a client by itself;
  - an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) is the IPv4 client `a.b.c.d`;
  - any other IPv6 address is grouped by its first P bits, where P is the IPv6 grouping prefix length;
  - when the connection address is missing or not an IP address (for example a Unix socket), the client is the single key `unknown`.
- **Sliding window:** a request accepted at time `t` counts toward a limit with window `W` while `now − t < W`, on a monotonic clock. A request exactly `W` seconds old no longer counts.

### Settings
Read once at startup and validated only while demo mode is on (003a RF-2). Integers use decimal digits only: no sign, no decimal point, no separators, no spaces.

| Setting (environment variable) | Default | Valid values |
|---|---|---|
| General request limit (`DEMO_RATE_GENERAL_LIMIT`) | 60 | integer 1 to 1,000 |
| General window (`DEMO_RATE_GENERAL_WINDOW_SECONDS`) | 60 | integer 1 to 3,600 |
| Write request limit (`DEMO_RATE_WRITE_LIMIT`) | 10 | integer 1 to 1,000 |
| Write window (`DEMO_RATE_WRITE_WINDOW_SECONDS`) | 60 | integer 1 to 3,600 |
| Per-client visitor-service cap (`DEMO_PER_CLIENT_VISITOR_CAP`) | 3 | integer 1 to 003a's global visitor-service cap |
| Trusted proxy hops (`DEMO_TRUSTED_PROXY_HOPS`) | 0 | integer 0 to 10 |
| Maximum forwarded entries read (`DEMO_MAX_FORWARDED_ENTRIES`) | 100 | integer 10 to 1,000 |
| IPv6 grouping prefix length (`DEMO_IPV6_PREFIX_LENGTH`) | 64 | integer 32 to 128 |
| Maximum tracked clients (`DEMO_RATE_MAX_CLIENTS`) | 10,000 | integer 100 to 100,000 |

## Functional requirements

### Settings and server options
- RF-1: WHILE demo mode is on, IF any 003b setting is unparsable or outside its valid values, or the per-client cap is greater than 003a's global visitor-service cap, THEN THE SYSTEM SHALL refuse to start with an error naming the environment variable (both variables for the cap rule). 003d's start command reports this together with the other settings errors.
- RF-2: WHILE demo mode is on, THE SYSTEM SHALL have 003d's project start command run the server, through the single place where 003d collects demo-mode server options (testable by checking the options passed to the server), with forwarding-header handling off, so the connection address is never rewritten, and with access logging off, so client addresses are not written to the server log. While demo mode is off, the server options stay as today.
- RF-3: THE SYSTEM SHALL rely on 003d RF-2 (the start marker) to guarantee that demo mode never runs under another start method, where the server might rewrite client addresses. This spec adds no second check.

### Rate limits
- RF-4: WHILE demo mode is on, WHEN a counted request arrives, THE SYSTEM SHALL evaluate it against the client's general limit and, for a write request, also against the client's write limit, both over sliding windows. Both limits are evaluated for a write, even when the first is already reached, so the 429 can name every exceeded limit. IF either limit is already reached, THEN THE SYSTEM SHALL answer HTTP 429 at once, before routing and before any other processing or error handling.
- RF-5: THE SYSTEM SHALL count only accepted requests:
  - a request answered 429 counts toward no limit;
  - an accepted request counts toward the general limit and, if it is a write request, toward the write limit, whatever happens next: any status, a client disconnect, or a 503 from 003c.
- RF-6: WHEN THE SYSTEM answers 429, it SHALL send:
  - the header `Retry-After: <S>`, where `<S>` is the time until every exceeded limit would accept the request: for each exceeded limit, `t_oldest + W − now`, rounded up to whole seconds and at least 1, taking the largest;
  - content type `application/json`, with the body `{"detail": "Rate limit exceeded: <limits>. Retry after <S> second(s)."}`:
    - `<limits>` lists each exceeded limit, in the order general then write, as `general limit (<L> requests per <W> s)` or `write limit (<L> requests per <W> s)`, joined by ` and `;
    - `second(s)` is `second` when `<S>` is 1 and `seconds` otherwise.

  A `HEAD` request gets the same status and headers, including `Retry-After`, with no body.
- RF-7: THE SYSTEM SHALL check rate limits before everything else in a request: before 003c's write gate and before every response in 003a RF-16. A 429 is never held by a reset.

### Client identification
- RF-8: WHILE demo mode is on, THE SYSTEM SHALL determine the client address as follows:
  - **Trusted proxy hops = 0 (the default):** the connection address. `X-Forwarded-For` is ignored.
  - **Trusted proxy hops = N > 0:** take all `X-Forwarded-For` header lines in order and join them with commas; split into entries; trim spaces; drop empty entries; keep only the last M entries, where M is the maximum forwarded entries read; take the Nth entry counted from the right (N = 1 is the rightmost, added by the nearest proxy).
  - **Accepted entry forms:**
    - IPv4 in strict dotted-quad form (four decimal numbers 0 to 255, no leading zeros), optionally followed by `:port`, never in brackets;
    - IPv6 in any RFC 4291 text form (hex case-insensitive, `::` compression, an embedded dotted quad that follows the same strict IPv4 rules), optionally in brackets, and with `:port` only when bracketed.

    A port must be decimal 1 to 65,535 without leading zeros, and is removed. An empty port (`1.2.3.4:`) is invalid.
  - **Rejected entry forms:** zone IDs and anything else.
  - **Fallback:** IF the header is missing, has fewer than N entries after dropping empty ones, or the selected entry is invalid, THEN use the connection address. An invalid selected entry is logged as a warning at most once a minute, without any address, so a misbehaving proxy is noticed.

  The address then becomes a client as in Definitions.

### Per-client visitor-service cap
- RF-9: WHILE demo mode is on, IF a create request comes from a client whose number of attributed visitor services is already at or above the per-client cap, THEN THE SYSTEM SHALL reject it with HTTP 409 and `{"detail": "Demo limit reached: at most <C> visitor services per client."}`.
  - The order is in 003a RF-16: global cap, then per-client cap, then per-host cap.
  - The per-client cap is checked before the spec 002 hostname check, then re-checked in 003a's atomic store step (003a RF-15), together with the global and per-host caps.
  - That one step covers the re-checks (in the same order as the first checks), storing the service, the commit, and recording the attribution, with no network lookup inside. It is serialized by the database (003a RF-15) and, for the in-memory attribution, within the process. It covers creates and updates that change a cap host alike. So the database and the attribution don't disagree, and concurrent requests can't exceed any cap.
  - The rate limiter uses its own, separate synchronization and never waits for this step (RNF-2).
  - Attribution can fall behind the database only after a restart (RF-11), or after the owner changes trusted proxy hops or the IPv6 prefix (Edge cases).
- RF-10: THE SYSTEM SHALL change attribution only as follows:
  - record it in the atomic step of a committed create (RF-9);
  - remove it inside the delete's commit step, serialized like the create step, so a create never sees a stale count;
  - clear it all when 003c reports a committed reset (003c RF-5).

  A request or reset that fails changes no attribution, and updates never change it.

### State and privacy
- RF-11: THE SYSTEM SHALL keep rate-limit counters and attribution in memory only:
  - For each client and limit, at most L timestamps (that limit's count) are kept. Memory therefore holds at most the maximum tracked clients × (general limit + write limit) timestamps, plus attribution.
  - Counters for a client are dropped once its windows hold no requests. This is checked when the client is next seen, and by a light background sweep every general window, so an idle process shrinks too. A sweep failure is logged and doesn't affect requests.
  - With the maximum settings the bound is about 2×10^8 timestamps, several gigabytes. The documentation states this worst case. The defaults stay small (RNF-5).
  - IF a new client would exceed the maximum tracked clients, THEN the counters of the client tracked longest without a request are dropped first. An active client is dropped only if more than the maximum of distinct clients arrive after its last request.
  - Attribution is never dropped by this rule. 003a's global cap bounds it.
  - Everything is lost on restart. Visitor services that survive a restart then count only toward the global and per-host caps until they are deleted or reset, so a client may hold more than the per-client cap until the next reset. Accepted.
- RF-12: THE SYSTEM SHALL NOT store client addresses in the database or write them to the application's own logs, including error logs from this feature. With the server options in demo mode, the server writes no access log (RF-2).
- RF-13: IF the rate limiter's counters fail unexpectedly for a request, THEN THE SYSTEM SHALL log the error (without the client address) and process the request without counting it (fail open), so a limiter fault never takes the API down. This deliberately trades some protection (constitution principle 7) for availability. The per-client cap is not covered: it is part of the create's atomic step, and an error there fails the create like any other unexpected error.
- RF-14: WHILE demo mode is off, THE SYSTEM SHALL not rate-limit, identify clients or apply the per-client cap.
- RF-15: THE SYSTEM SHALL document:
  - the limits and defaults, the sliding-window rule, the 429 response and the request classification;
  - client identification, and how to set trusted proxy hops for a deployment (and why the app must then be reachable only through the proxy);
  - the server options in demo mode (RF-2), and that demo mode runs only through 003d's start command;
  - that platform health probes should use `/health`, because other paths are counted;
  - the `unknown` client;
  - the per-client cap and its restart exception;
  - the memory bound and the eviction behavior;
  - the in-memory, single-process state.

  The detailed reference goes in the architecture document. The product SPEC and the README "Running a public demo" section (created by 003a) summarize it and link there.

## Non-functional requirements
- RNF-1: With demo mode off, this spec changes nothing. All existing tests pass with unchanged outcomes, apart from changes made by the other demo specs (003a RF-14 and RF-19, 003c RF-10, and 003d's start command), and the API schema document is identical.
- RNF-2: Rate limiting never makes a request wait: each counted request is accepted or rejected immediately. A wait that 003c imposes on an accepted write is not part of rate limiting.
- RNF-3: Counters, attribution and the per-client cap are safe under concurrent requests.
- RNF-4: The window logic, the `X-Forwarded-For` parsing and the client grouping are pure functions with tests written first (constitution principle 4). Time-based behavior is tested with an injectable monotonic clock, never by waiting.
- RNF-5: **Benchmarks** (measured and recorded in the validation notes; not CI gates):
  - the added time per request for the limit check is under 1 ms at the 95th percentile;
  - with 10,000 distinct clients at default limits, the process's memory grows by less than 50 MB, and tracked counters shrink to empty once the windows pass.
- RNF-6: The server options are passed through 003d's start command. The container change itself belongs to 003d and its approval.
- RNF-7: The spec 000 to 003c and 003d quality gates stay green.
- RNF-8: Automated tests cover, at minimum:
  - RF-1, including per-client cap > global cap;
  - RF-2: the server options in demo mode on and off;
  - tests that need a client address inject it, because the test client's address isn't an IP address (it would be the `unknown` client);
  - each limit at `W − ε`, `W` and `W + ε`;
  - 429 not counting, and a write rejected by the general limit not counting toward the write limit;
  - `Retry-After` and the message for one limit, both limits and `S = 1`, and a `HEAD` 429 with `Retry-After` and no body;
  - the classification of liveness, `/health/`, `/health?x=1`, docs, unknown paths, `OPTIONS` and `HEAD`;
  - RF-7 precedence, including a 429 during a reset;
  - RF-8:
    - 0 hops with a forged header;
    - N hops with several lines, spaces, empty entries, more than M entries, IPv4 and bracketed IPv6 ports, IPv6 with an embedded dotted quad, uppercase hex, IPv4 with leading zeros, port `0` and `08080`, a short header, an invalid entry and a zone ID;
  - a connection address with a zone ID, IPv4-mapped addresses, IPv6 grouping at the configured prefix, and the `unknown` client;
  - the per-client cap at and above its boundary and under concurrency;
  - attribution changing only on committed creates, deletes and resets;
  - counter eviction at the maximum without dropping attribution, and the sweep shrinking an idle process;
  - RF-13 fail-open;
  - demo mode off changing nothing.

## Edge cases
- Opening the interactive docs makes about 3 counted requests (the page, the API schema document and the browser's `/favicon.ico`). With the default of 60 per minute, normal browsing stays far below the limit.
- Behind a proxy with trusted proxy hops left at 0: every visitor shares the proxy's address and one budget. The docs explain how to set the hop count.
- With hops = N > 0, a client that reaches the app directly can forge `X-Forwarded-For`. The docs say the hop count must match the real proxy chain and the app must be reachable only through it.
- A proxy that replaces `X-Forwarded-For` instead of appending, with N set too high: the header has fewer than N entries, so every visitor falls back to the proxy's address. This is a misconfiguration, and the docs cover it.
- An oversized `X-Forwarded-For` header: only the last M entries are read. If the server rejects the header for size, the request never reaches the app.
- Local runs with Docker Desktop or WSL port publishing: the app may see the Docker network gateway as the connection address, so all local requests share one client. Manual validation runs requests from inside the Docker network, or uses a proxy with N hops.
- Running behind a Unix socket with hops = 0: every request is the `unknown` client, with one budget and one per-client cap. Documented. Use a TCP proxy with N hops instead.
- Many IPv6 addresses inside one prefix count as one client. Different IPv4 addresses or IPv6 prefixes get separate budgets. Accepted (CAPTCHA and bot detection are out of scope).
- IPv6 forms that embed IPv4 (NAT64, 6to4, Teredo, IPv4-compatible) are grouped as IPv6 by prefix. Only IPv4-mapped addresses become IPv4. Accepted and documented.
- A flood of more distinct addresses than the maximum evicts idle clients' counters first. An evicted client starts a fresh window and may pass sooner than its `Retry-After` said. Accepted.
- The owner changes trusted proxy hops or the IPv6 prefix: attribution recorded under the old keys stays until those services are deleted or reset.
- A long window (for example 3,600 s, general or write) can give a long `Retry-After`. That follows from the owner's settings and is documented.
- `/health` is never limited. It touches neither the database nor the scheduler, so a flood of it can't reach them.
- A visitor service attributed to client A is deleted by client B: allowed (003a RF-12). A's count drops when the delete commits.
- A deactivated visitor service still counts toward its client's cap until deleted or reset.

## Out of scope
- Rate limits or state shared across processes or instances (003d).
- Authentication, API keys, accounts (roadmap 007).
- CAPTCHA, bot detection, IP allow or deny lists.
- Different limits per endpoint beyond general and write.
- Persisting counters or attribution.
- Relying on the server's own forwarding-header handling.

## Completion criteria
- With demo mode on, a client over either limit gets 429 with the correct `Retry-After` and message, and gets through once the window allows, in automated tests (injectable clock) and in a manual run against the running stack.
- Client identification behaves as RF-8 with 0 and N hops, including a forged header and every parsing case in RNF-8.
- The per-client cap returns its 409 in the 003a RF-16 order and is released on delete and after a committed reset.
- With demo mode on, the server runs with forwarding-header handling disabled and access logging disabled (through 003d's start command).
- With demo mode off, all existing tests pass unchanged and the API schema document is identical.
- The RNF-5 benchmarks are measured and recorded. The docs are updated per RF-15. The quality gates are green in CI.

## Decisions made in revision
Earlier decisions (D1 to D12, G1 to G15) still apply unless refined here. All of these are mine, for your review; none of your six pass-3 decisions changed this spec's scope.

- **K1 (1.1, 2.2) – project start command:**
  - one Python start command reads `DEMO_MODE` with the app's own rules and sets the server's forwarding-header and access-log options itself (RF-2), which is verified possible with uvicorn 0.42.0;
  - the shell doesn't parse booleans, so the two can't disagree;
  - if a platform overrides the start command, the app detects it and refuses in demo mode (RF-3), so RF-2 becomes verifiable.
- **K2 (1.2, 3.4) – one atomic step:** it covers the cap re-checks, the store, the commit and the attribution record, with no network lookup (RF-9). The write gate in 003c keeps resets out of it.
- **K3 (1.3) – memory:** at most L timestamps per limit and client, and lower maximum ranges (limits up to 1,000, tracked clients up to 100,000), so the bound is a stated number.
- **K4 (2.1) – eviction claim corrected:** idle clients are evicted first, and an active client only after more than the maximum of newer distinct clients. An early pass after eviction is accepted.
- **K5 (2.3) – order with 003c:** the 429 check comes first, then 003c's write gate (RF-7).
- **K6 (2.4) – restart exception:** stated in RF-9 and RF-11 and accepted. A reset clears it.
- **K7 (1.4) – no required settings:** "missing where required" removed. Every 003b setting has a default.
- **K8 (1.5, 1.6) – address forms:** IPv6 text forms per RFC 4291, ports decimal without leading zeros, and zone IDs removed from the connection address but invalid in `X-Forwarded-For` entries.
- **K9 (1.7, 1.8) – request handling:** only HTTP requests are counted. A 429 is produced before routing and error handling, and `HEAD` gets `Retry-After`.
- **K10 (1.9, 4.1) – settings:** environment variable names are given. The 100-entry cap is now a setting. The minimum `Retry-After` of 1 is HTTP semantics, not a tunable.
- **K11 (3.3) – disconnects:** an accepted request counts even if the client disconnects (RF-5).
- **K12 (4.2) – pure functions:** the parser, the grouping and the window logic are pure and tested first (RNF-4).
- **K13 (4.5) – limiter failure:** fail open, with an error log (RF-13), to keep the API up. Accepted risk: a limiter bug briefly disables limits.
- **K14 (2.6, 3.9, 3.5, 3.6, 3.8) – documented edge cases:** README section ownership, health probes, key changes after a config change, long `Retry-After` values, and replacing proxies.

- **P1 (owner, decision 1 of pass 4) – start command moved to 003d:** this spec now only supplies the demo-mode server options (RF-2) and relies on 003d's start marker (RF-3). K1 is superseded. This resolves findings 1.1, 1.2, 1.3 and 3.1 (detection, `.env` parsing, command scope, process model), which 003d now owns.
- **P2 (1.4) – process-wide serialization:** the atomic step is serialized across the process and contains no network lookup. The limiter has its own synchronization and never waits for it.
- **P3 (1.5) – shrinking:** lazy pruning plus a light sweep every general window, so an idle process frees its memory.
- **P4 (1.6, 4.1) – fail-open scope:** counters only, uncounted, logged without the address. The tension with principle 7 is stated and accepted.
- **P5 (1.7, 1.7b) – parsing and evaluation:** bracketed IPv4 is invalid, an embedded dotted quad follows the strict IPv4 rules, an empty port is invalid, and both limits are evaluated for writes.
- **P6 (2.1) – unreachable rule removed:** "forwarded entries ≥ hops" could never fail with the given ranges, so the rule and its test are gone.
- **P7 (2.2, 2.3, 3.3, 3.4, 3.6) – fixes:**
  - the connection-address reference is corrected;
  - the memory bound says "at most";
  - any method on `/health` is liveness;
  - tests inject client addresses;
  - error logs exclude client addresses.
- **P8 (3.2) – eviction:** added to the docs list.

- **U1 (1.1) – host-changing updates:** also go through the atomic step. The database serializes it (003a RF-15), and the process serializes the in-memory attribution.
- **U2 (1.2) – deletes:** attribution is removed inside the delete's commit step, so there's no window with a stale count.
- **U3 (1.3) – re-check order:** the same order as the first checks.
- **U4 (1.4) – server options seam:** one place in 003d's start command collects them, so tests can assert what the server receives.
- **U5 (1.5) – sweep:** a light background task. Its failures are logged and don't touch requests.
- **U6 (1.6, 3.1) – memory:** the benchmark criterion is under 50 MB growth for 10,000 clients at defaults, and the maximum-settings worst case is documented.
- **U7 (1.7, 2.1, 2.2, 2.3) – fixes:**
  - the docs-browsing count, including the favicon;
  - unambiguous completion wording;
  - RNF-1 and the RNF-8 reference corrected;
  - the attribution exceptions listed in full.
- **U8 (3.2) – invalid proxy entries:** logged as a warning at most once a minute, without addresses.
- **U9 (3.3) – reset notification:** synchronous, before 003c opens the gate (003c RF-5).
- **U10 (confirmation pass) – server options:** the place that collects them is now defined in 003d RF-1. RNF-1 lists every change made by the other demo specs.

## Open questions
- (none)
