# 003b - Demo mode: rate limiting and client identification

Status: draft (revised 2026-11-24 after `/sdd-clarify` pass 2)

## Context and goal
Specs 003a and 003c make the API deployable as a public demo: seeded services, protection, a global cap and a per-host cap, a single enforced process, and a periodic reset. They don't limit what one client can do:
- One client can send requests as fast as it likes and flood the API or the database.
- One client can create visitor services up to the global cap and keep every other visitor out until the next reset.

Goal: while demo mode is on, every client gets a request budget, with a smaller budget for writes, and its own cap on visitor services. Clients are identified reliably behind a hosting proxy, and a forged header can't escape the limits. While demo mode is off, nothing in this spec applies.

This spec depends on 003a (demo mode, caps, error order) and 003c (single process, the reset hook). Implementation order: 003a, then 003c, then 003b.

## Users
- **Visitor:** can explore the demo at a normal pace, and gets a clear 429 with a wait time if it goes too fast.
- **Owner/operator:** is protected from one client flooding the API or taking all visitor-service slots, and configures the limits and the proxy setup.

## User stories
- As a visitor, browsing at a normal pace, including opening the interactive docs, never hits a limit. Too many requests give a 429 that says how long to wait.
- As a visitor, I can create a few services of my own, even when another visitor has been busy.
- As the owner, behind my host's proxy each visitor is counted separately, and a client cannot escape its limits by forging a header.

## Definitions
- **Liveness request:** a request whose path is exactly `/health`, with any method and any query string. `/health/` and `/Health` are not liveness requests.
- **Write request:** a request with method POST, PUT, PATCH or DELETE, on any path, including unknown paths. It is classified by method alone, before any processing.
- **Counted request:** every request that is not a liveness request. That includes `GET /`, the interactive docs and API schema document, unknown paths, and `HEAD` and `OPTIONS` requests.
- **Connection address:** the IP address of the network peer as the application receives it. The server must be configured so it never replaces this address from forwarding headers (RF-11).
- **Client:** the key a request is counted under, derived from the client address (RF-6):
  - an IPv4 address is a client by itself;
  - an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) is the IPv4 client `a.b.c.d`;
  - any other IPv6 address is grouped by its first P bits, where P is the IPv6 grouping prefix length;
  - when the connection address is missing or not an IP address (for example a Unix socket), the client is the single key `unknown`.
- **Sliding window:** a request accepted at time `t` counts toward a limit with window `W` while `now − t < W`, on a monotonic clock. A request exactly `W` seconds old no longer counts.

### Settings
Read once at startup and validated only while demo mode is on (003a RF-2).

| Setting | Default | Valid range |
|---|---|---|
| General request limit | 60 per window | 1 to 100,000 |
| General window | 60 seconds | 1 to 3,600 seconds |
| Write request limit | 10 per window | 1 to 100,000 |
| Write window | 60 seconds | 1 to 3,600 seconds |
| Per-client visitor-service cap | 3 | 1 to 003a's global visitor-service cap |
| Trusted proxy hops | 0 | 0 to 10 |
| IPv6 grouping prefix length | 64 | 32 to 128 |
| Maximum tracked clients | 10,000 | 100 to 1,000,000 |

## Functional requirements

### Settings
- RF-1: WHILE demo mode is on, IF any 003b setting is missing where required, unparsable or outside its valid range, or the per-client cap is greater than 003a's global visitor-service cap, THEN THE SYSTEM SHALL refuse to start with an error naming the setting (both settings for the cap rule).

### Rate limits
- RF-2: WHILE demo mode is on, WHEN a counted request arrives, THE SYSTEM SHALL check it against the client's general limit and, for a write request, also against the client's write limit, both over sliding windows. IF either limit is already reached, THEN THE SYSTEM SHALL answer HTTP 429 without processing the request.
- RF-3: THE SYSTEM SHALL count only accepted requests:
  - a request answered 429 counts toward no limit, so a write rejected by the general limit doesn't count toward the write limit;
  - an accepted request counts toward the general limit and, if it is a write request, toward the write limit, whatever status its processing returns.
- RF-4: WHEN THE SYSTEM answers 429, it SHALL send:
  - the header `Retry-After: <S>`, where `<S>` is, for each exceeded limit, the time until its oldest counted request leaves the window (`t_oldest + W − now`), rounded up to whole seconds, at least 1, taking the largest value when both limits are exceeded;
  - content type `application/json`, with the body `{"detail": "Rate limit exceeded: <limits>. Retry after <S> second(s)."}`, where `<limits>` is `general limit (<L> requests per <W> s)`, `write limit (<L> requests per <W> s)`, or both in that order joined by ` and `, and `second(s)` is `second` when `<S>` is 1 and `seconds` otherwise. A `HEAD` request gets the same status and headers with no body.
- RF-5: THE SYSTEM SHALL check rate limits before any other check of a request, so a 429 comes ahead of every response in 003a RF-15. A rejected write is never delayed by a reset (003c RF-10).

### Client identification
- RF-6: WHILE demo mode is on, THE SYSTEM SHALL determine the client address as follows:
  - **Trusted proxy hops = 0 (the default):** the connection address. `X-Forwarded-For` is ignored.
  - **Trusted proxy hops = N > 0:** take all `X-Forwarded-For` header lines in order and join them with commas; split into entries; trim spaces; drop empty entries; keep at most the last 100 entries; take the Nth entry counted from the right (N = 1 is the rightmost, added by the nearest proxy).
  - **Accepted entry forms:** a strict dotted-quad IPv4 address, optionally with `:port`; an IPv6 address in standard text form, optionally in brackets, and with `:port` only when bracketed. The port must be 1 to 65,535 and is removed.
  - **Rejected entry forms:** leading zeros, non-dotted or short IPv4 forms, zone IDs and anything else are invalid.
  - **Fallback:** IF the header is missing, has fewer than N entries after dropping empty ones, or the selected entry is invalid, THEN use the connection address.

  The address then becomes a client as in Definitions.

### Per-client visitor-service cap
- RF-7: WHILE demo mode is on, IF a create request comes from a client whose number of attributed visitor services is already at or above the per-client cap, THEN THE SYSTEM SHALL reject it with HTTP 409 and `{"detail": "Demo limit reached: at most <C> visitor services per client."}`. The order is in 003a RF-15: global cap, then per-client cap, then per-host cap. The check and the create SHALL be one atomic unit together with 003a's caps, so concurrent requests can't exceed any cap.
- RF-8: THE SYSTEM SHALL attribute a visitor service to its client only when its create commits, remove that attribution only when its delete commits, and clear all attribution only when a reset commits (003c RF-8). A request or reset that fails changes no attribution. Updates never change attribution.

### State, server configuration and privacy
- RF-9: THE SYSTEM SHALL keep rate-limit counters and attribution in memory only:
  - Counters for a client are dropped once its windows hold no requests.
  - IF tracking another client's counters would exceed the maximum tracked clients, THEN the counters of the client tracked longest without a request are dropped first.
  - Attribution is never dropped by this rule. It is bounded by 003a's global cap.
  - Everything is lost on restart. Visitor services that survive a restart then count only toward the global and per-host caps until they are deleted or reset.
- RF-10: THE SYSTEM SHALL NOT store client addresses in the database or write them to the application's own logs.
- RF-11: WHILE demo mode is on, WHEN the API runs through the project's container image or documented start command, THE SYSTEM SHALL run the server with forwarding-header handling off, so the connection address is never rewritten, and with access logging off, so client addresses are not written to the server log.
- RF-12: WHILE demo mode is off, THE SYSTEM SHALL not rate-limit, identify clients or apply the per-client cap, and the server keeps today's behavior.
- RF-13: THE SYSTEM SHALL document:
  - the limits and defaults, the sliding-window rule, the 429 response and the request classification;
  - client identification, and how to set trusted proxy hops for a deployment (and why the app must then be reachable only through the proxy);
  - the server configuration in demo mode (RF-11);
  - the `unknown` client;
  - the per-client cap;
  - the in-memory, single-process state.

  The detailed reference goes in the architecture document. The product SPEC and the README "Running a public demo" section summarize it and link there.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change. All existing tests pass with unchanged outcomes, and the API schema document is identical.
- RNF-2: Rate limiting never makes a request wait: each counted request is accepted or rejected immediately.
- RNF-3: Counters, attribution and the per-client cap are safe under concurrent requests. No race lets a client exceed a limit or cap.
- RNF-4: Time-based behavior (windows, boundaries, `Retry-After`) is tested with an injectable monotonic clock, never by waiting.
- RNF-5: **Benchmarks** (measured and recorded in the validation notes; not CI gates):
  - the added time per request for the limit check is under 1 ms at the 95th percentile;
  - with 10,000 distinct clients, tracked counters stay within the maximum and shrink to empty once the windows pass.
- RNF-6: Any change to the container image or start command (RF-11) needs owner approval in the plan (AGENTS.md: Docker config is ask-first).
- RNF-7: The spec 000 to 003c quality gates stay green.
- RNF-8: Automated tests cover, at minimum:
  - RF-1, including per-client cap > global cap;
  - each limit at `W − ε`, `W` and `W + ε`;
  - 429 not counting, and a write rejected by the general limit not counting toward the write limit;
  - `Retry-After` and the message for one limit, both limits and `S = 1`, and a `HEAD` 429 with no body;
  - the classification of liveness, `/health/`, `/health?x=1`, docs, unknown paths, `OPTIONS` and `HEAD`;
  - RF-5 precedence;
  - RF-6:
    - 0 hops with a forged header;
    - N hops with several lines, spaces, empty entries, more than 100 entries, ports, bracketed IPv6 with and without a port, leading zeros, a short header, an invalid entry and a zone ID;
  - IPv4-mapped addresses, IPv6 grouping at the configured prefix, and the `unknown` client;
  - the per-client cap at and above its boundary and under concurrency;
  - attribution recorded only on commit, released on delete, cleared only after a committed reset, and unchanged by a failed create or reset;
  - counter eviction at the maximum without dropping attribution;
  - demo mode off changing nothing.

## Edge cases
- Opening the interactive docs makes about 2 counted requests (the page and the API schema document). With the default of 60 per minute, normal browsing stays far below the limit.
- Behind a proxy with trusted proxy hops left at 0: every visitor shares the proxy's address and one budget. The docs explain how to set the hop count.
- With hops = N > 0, a client that reaches the app directly can forge `X-Forwarded-For`. The docs say the hop count must match the real proxy chain and the app must be reachable only through it.
- Local runs with Docker Desktop or WSL port publishing: the app may see the Docker network gateway as the connection address, so all local requests share one client. Manual validation runs requests from inside the Docker network, or uses a proxy with N hops (Decisions).
- Running behind a Unix socket with hops = 0: every request is the `unknown` client, with one budget and one per-client cap. Documented. Use a TCP proxy with N hops instead.
- Many IPv6 addresses inside one prefix count as one client. Different IPv4 addresses or IPv6 prefixes get separate budgets. Accepted (CAPTCHA and bot detection are out of scope).
- IPv6 forms that embed IPv4 (NAT64, 6to4, Teredo, IPv4-compatible) are grouped as IPv6 by prefix. Only IPv4-mapped addresses become IPv4. Accepted and documented.
- A flood of distinct addresses evicts other clients' counters (RF-9). An evicted client starts a fresh window, which gives at most one more window's budget. Attribution is never evicted, so the per-client cap still holds.
- A long write window (for example 3,600 s) can give a long `Retry-After`. That follows from the owner's settings and is documented.
- A visitor service attributed to client A is deleted by client B: allowed (003a RF-11). A's count drops when the delete commits.
- A deactivated visitor service still counts toward its client's cap until deleted or reset.
- `HEAD /health` or `GET /health` from the platform's health check: never limited.

## Out of scope
- Rate limits or state shared across processes or instances (003c RNF-2).
- Authentication, API keys, accounts (roadmap 007).
- CAPTCHA, bot detection, IP allow or deny lists.
- Different limits per endpoint beyond general and write.
- Persisting counters or attribution.
- Relying on the server's own forwarding-header handling (Decisions G2).

## Completion criteria
- With demo mode on, a client over either limit gets 429 with the correct `Retry-After` and message, and gets through once the window allows, in automated tests (injectable clock) and in a manual run against the running stack.
- Client identification behaves as RF-6 with 0 and N hops, including a forged header and every parsing case in RNF-8.
- The per-client cap returns its 409 in the 003a RF-15 order and is released on delete and after a committed reset.
- In demo mode, the container's server doesn't rewrite the client address from forwarding headers and writes no access log.
- With demo mode off, all existing tests pass unchanged and the API schema document is identical.
- The RNF-5 benchmarks are measured and recorded. The docs are updated per RF-13. The quality gates are green in CI.

## Decisions made in revision
Pass 1 decisions D1 to D12 still apply except where refined below. Items marked "owner" came from your answers.

- **G1 (owner) – split:** this spec keeps rate limiting, client identification and the per-client cap.
  - It depends on 003a and on the new 003c.
- **G2 (owner) – client identity is owned by the app (2.1, 4.2).**
  - Verified: uvicorn 0.42.0 has `proxy_headers` on by default and trusts `FORWARDED_ALLOW_IPS`, defaulting to `127.0.0.1`. It would otherwise rewrite the client address from `X-Forwarded-For`.
  - In demo mode the server runs with forwarding-header handling off (RF-11), and the hop-count rule (RF-6) is the only source of client identity.
  - The Docker change needs approval in the plan (RNF-6).
- **G3 (owner) – access log:** off in demo mode (RF-11), so the shipped image writes no client addresses (2.4).
- **G4 (owner) – settings validation:** invalid 003b settings refuse startup, and per-client cap ≤ global cap is checked (RF-1).
  - The earlier "write limit ≤ general limit" range is dropped, because the two use different windows and the rule meant nothing (1.4).
- **G5 (1.1) – window boundary:** a request counts while `now − t < W`, on a monotonic clock (3.10). `Retry-After` is `t_oldest + W − now`, rounded up, at least 1, and the largest value when both limits are hit.
- **G6 (1.2, 3.8) – eviction:** applies to rate-limit counters only. Attribution is never evicted, and the global cap bounds it.
- **G7 (1.5, 3.12) – 429 response:** an exact message with singular/plural, the order "general and write", JSON content type, and no body for `HEAD`.
- **G8 (1.6, 3.6) – `unknown` client:** applies when the connection address is missing or not an IP address (for example a Unix socket). Documented, with the shared budget as a known consequence.
- **G9 (1.7, 3.3) – attribution timing:** changes only on commit of a create, a delete or a reset (RF-8). A failed request or reset changes nothing. The per-client check is atomic with 003a's caps (RF-7, 3.2).
- **G10 (2.2) – error order:** 003a RF-15 now includes the per-client 409 between the global and per-host caps.
- **G11 (2.3, 3.9) – request classification:**
  - Counted requests include docs, the API schema document, unknown paths, `HEAD` and `OPTIONS`.
  - Liveness is the exact path `/health`, with any query string.
  - Writes are classified by method, including on unknown paths.
  - Opening the docs costs about 2 requests, well under the default.
- **G12 (3.4) – `X-Forwarded-For` parsing:** lines joined, entries trimmed, empty entries dropped, at most the last 100 entries kept, strict IPv4 (no leading zeros or non-dotted forms) and standard IPv6, ports only in the stated forms, and zone IDs invalid. Strict canonical forms keep one client from turning into two keys.
- **G13 (3.5) – embedded IPv4 forms:** only IPv4-mapped addresses become IPv4. Other forms are grouped as IPv6. Documented. A prefix below 64 can merge unrelated users, so the range starts at 32 and the docs explain the trade-off.
- **G14 (3.1) – Docker Desktop/WSL:** the manual validation runs from inside the Docker network, or behind a proxy with N hops, so the gateway address doesn't hide clients.
- **G15 (3.7) – long `Retry-After`:** follows from the owner's window settings. Documented.

## Open questions
- (none)
