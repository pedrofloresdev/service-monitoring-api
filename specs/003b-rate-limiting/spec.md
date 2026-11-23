# 003b - Rate limiting (demo mode)

Status: draft (split from 003-demo-mode on 2026-11-23 after `/sdd-clarify` pass 1)

## Context and goal
Spec 003a makes the API deployable as a public demo: seeded services, protection, a global cap and a per-host cap on visitor services, and a periodic reset. It does not limit how much one client can do:
- One client can send requests as fast as it likes and flood the API or the database.
- One client can create visitor services up to the global cap and keep every other visitor out until the next reset.

Goal: while demo mode is on, every client gets a request budget, with a smaller budget for writes, and its own cap on visitor services. A client is identified reliably behind a hosting proxy without trusting forgeable headers. While demo mode is off, nothing in this spec applies.

This spec depends on spec 003a (demo mode, the reset, the single-process design). It is part 2 of 2. Both are needed before a public deployment.

## Users
- **Visitor:** can explore the demo at a normal pace, and gets a clear 429 with a wait time if it goes too fast.
- **Owner/operator:** is protected from one client flooding the API or taking all visitor-service slots, and can configure the limits and the proxy setup.

## User stories
- As a visitor, browsing at a normal pace never hits a limit. If I send too many requests, I get a 429 that tells me how long to wait.
- As a visitor, I can create a few services of my own, even when another visitor has been busy.
- As the owner, behind my host's proxy each visitor is counted separately, and a client cannot escape its limits by forging a header.

## Definitions
- **Write request:** a request with method POST, PUT, PATCH or DELETE, on any path. It is classified by method alone, before the request is processed.
- **Liveness request:** a request whose path is exactly `/health`, with any method.
- **Client:** the key a request is counted under, determined as in RF-5:
  - an IPv4 address is a client by itself;
  - an IPv6 address is grouped by its first P bits, where P is the IPv6 grouping prefix length;
  - an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) is treated as the IPv4 address `a.b.c.d`;
  - when no address is available at all, the client is the single key "unknown".
- **Sliding window:** a client's requests counted over the last W seconds, measured from the current moment.

### Settings
Read once at startup and validated only while demo mode is on, as in spec 003a.

| Setting | Default | Valid range |
|---|---|---|
| General request limit | 60 per window | 1 to 100,000 |
| General window | 60 seconds | 1 to 3,600 seconds |
| Write request limit | 10 per window | 1 to the general limit |
| Write window | 60 seconds | 1 to 3,600 seconds |
| Per-client visitor-service cap | 3 | 1 to spec 003a's global cap |
| Trusted proxy hops | 0 | 0 to 10 |
| IPv6 grouping prefix length | 64 | 32 to 128 |
| Maximum tracked clients | 10,000 | 100 to 1,000,000 |

## Functional requirements

### Rate limits
- RF-1: WHILE demo mode is on, WHEN a request that is not a liveness request arrives, THE SYSTEM SHALL check it against the client's general limit and, for a write request, also against the client's write limit, both over sliding windows. IF either limit is already reached, THEN THE SYSTEM SHALL answer HTTP 429 without processing the request.
- RF-2: THE SYSTEM SHALL count only accepted requests: a request answered 429 counts toward no limit. An accepted request counts toward the general limit and, if it is a write request, toward the write limit, whatever status its processing returns.
- RF-3: WHEN THE SYSTEM answers 429, it SHALL include:
  - a `Retry-After` header with the whole number of seconds, rounded up and at least 1, until every exceeded limit would accept the request again (the longest wait when both are exceeded);
  - the body `{"detail": "Rate limit exceeded: <limits>. Retry after <S> seconds."}`, where `<limits>` is `general limit (L per W s)`, `write limit (L per W s)`, or both joined by ` and `, and `<S>` equals `Retry-After`.
- RF-4: THE SYSTEM SHALL check rate limits before any other check of a request, so a 429 comes ahead of every response listed in spec 003a RF-13.

### Client identification
- RF-5: WHILE demo mode is on, THE SYSTEM SHALL determine a request's client address as follows:
  - With trusted proxy hops = 0 (the default): the address of the network connection. `X-Forwarded-For` is ignored.
  - With trusted proxy hops = N > 0: join all `X-Forwarded-For` header lines in order with commas, split into entries, trim spaces, and take the Nth entry counted from the right (N = 1 is the rightmost, the address added by the nearest proxy).
  - An entry may carry a port (`1.2.3.4:80`, `[2001:db8::1]:443`); the port is removed.
  - IF the header is missing, has fewer than N entries, or the selected entry is not a valid IPv4 or IPv6 address (an IPv6 zone ID counts as invalid), THEN use the connection address.

  The address is then turned into a client as in Definitions.

### Per-client visitor-service cap
- RF-6: WHILE demo mode is on, WHEN a request creates a visitor service, THE SYSTEM SHALL attribute it to the request's client. IF the number of existing visitor services attributed to that client already equals the per-client cap, THEN THE SYSTEM SHALL reject the request with HTTP 409 and `{"detail": "Demo limit reached: at most <C> visitor services per client."}`. The cap SHALL hold under concurrent requests.
- RF-7: THE SYSTEM SHALL place the per-client cap 409 in spec 003a's create order between the global cap and the per-host cap: 409 global cap, then 409 per-client cap, then 409 per-host cap.
- RF-8: WHEN a visitor service is deleted, by a request or by a reset (spec 003a RF-15), THE SYSTEM SHALL remove its attribution. WHEN a reset runs, THE SYSTEM SHALL clear all attribution.

### State and privacy
- RF-9: THE SYSTEM SHALL keep rate-limit counters and per-client attribution in memory only.
  - A client's counters are dropped once its windows hold no requests.
  - IF the number of tracked clients would exceed the maximum, THEN the client tracked longest without a request is dropped first.
  - Attribution is lost on restart. Visitor services that survive a restart (spec 003a RF-14) then count only toward the global and per-host caps until they are deleted or reset.
- RF-10: THE SYSTEM SHALL NOT store client addresses in the database or write them to the application's own logs. Server access logs are part of the deployment configuration, and the documentation says so.
- RF-11: WHILE demo mode is off, THE SYSTEM SHALL not rate-limit, identify clients or apply the per-client cap.
- RF-12: THE SYSTEM SHALL document the limits and their defaults, the sliding-window rule, the 429 response, client identification, how to set trusted proxy hops for a deployment (and why the app must then be reachable only through the proxy), the per-client cap, and the in-memory, single-process nature of the state. The detailed reference goes in the architecture document. The product SPEC and the README "Running a public demo" section summarize it and link there.

## Non-functional requirements
- RNF-1: With demo mode off, behavior does not change. All existing tests pass with unchanged outcomes, and the API schema document is identical.
- RNF-2: Rate limiting never makes a request wait: each request is accepted or rejected immediately.
- RNF-3: Counters and attribution are safe under concurrent requests. No race lets a client exceed a limit or cap.
- RNF-4: All time-based behavior (windows, `Retry-After`, expiry) is tested with an injectable clock, never by waiting.
- RNF-5: **Benchmarks** (measured and recorded in the validation notes; not CI gates):
  - the added time per request for the limit check is under 1 ms at the 95th percentile;
  - with 10,000 distinct clients the tracked state stays within the maximum, and it shrinks back to empty once the windows pass.
- RNF-6: The spec 000 to 003a quality gates stay green.
- RNF-7: Automated tests cover, at minimum:
  - the general and write limits at their exact boundary;
  - sliding-window expiry;
  - 429 not counting;
  - a write rejected by the general limit not counting toward the write limit;
  - `Retry-After` and the message for one limit and for both;
  - liveness never limited;
  - RF-4 precedence;
  - RF-5 with 0 hops (a forged header ignored) and N hops (several header lines, spaces, ports, a short header, an invalid entry, a zone ID);
  - IPv4-mapped addresses, IPv6 grouping at the configured prefix, and the "unknown" client;
  - the per-client cap at its boundary and under concurrency, RF-7 order, and attribution released on delete and cleared on reset;
  - state eviction at the maximum;
  - demo mode off changing nothing.

## Edge cases
- Behind a proxy with trusted proxy hops left at 0: every visitor shares the proxy's address and one budget. The documentation explains how to set the hop count.
- With hops = N > 0, a client that reaches the app directly can forge `X-Forwarded-For`. The documentation says the hop count must match the real proxy chain and the app must be reachable only through it.
- A client with many IPv6 addresses inside one prefix: counted as one client.
- A client spread over many IPv4 addresses or IPv6 prefixes gets one budget per address or prefix. That is accepted; CAPTCHA and bot detection are out of scope.
- An attacker floods with many distinct addresses to evict other clients' state (RF-9). An evicted client simply starts a fresh window, so nobody gets more than one window's budget at a time from eviction. Accepted.
- A visitor deletes one of its services and creates another: allowed while the per-client count is below the cap.
- A visitor service is attributed to client A, and client B deletes it: allowed (spec 003a RF-9). A's count drops.
- A restart between resets: counters and attribution are lost (RF-9), and the global and per-host caps still apply.
- `HEAD /health` or `GET /health` from the platform's health check: never limited.

## Out of scope
- Rate limits or state shared across several processes or instances (spec 003a RNF-3: single process).
- Authentication, API keys, accounts (roadmap 007).
- CAPTCHA, bot detection, IP allow or deny lists.
- Different limits per endpoint, beyond general and write.
- Persisting counters or attribution.

## Completion criteria
- With demo mode on, a client over either limit gets 429 with the correct `Retry-After` and message, and gets through again once the window allows, in automated tests (injectable clock) and in a manual run against the running stack.
- Client identification behaves as RF-5 with 0 and N hops, including a forged header and the parsing edge cases.
- The per-client cap returns its 409 at the boundary, in the RF-7 order, and is released on delete and reset.
- With demo mode off, all existing tests pass unchanged and the API schema document is identical.
- The RNF-5 benchmarks are measured and recorded. The docs are updated per RF-12. The quality gates are green in CI.

## Decisions made in revision
Items marked "owner" came from your answers; the others are mine, for you to review.

- **D1 (owner) – split:** this spec holds rate limiting and client identification.
  - **Deviation:** it also holds the per-client visitor-service cap (moved from 003a), because that cap depends on client identification.
- **D2 (owner) – limits:** sliding window, 60 requests per minute general, 10 writes per minute; 429s don't count; `Retry-After` uses the longest wait when both limits are hit; injectable clock in tests.
- **D3 (owner) – IPv4-mapped addresses** are treated as IPv4. The IPv6 grouping prefix is a setting (default 64), which resolves finding 4.4.
- **D4 (owner) – performance targets:** documented benchmarks, not CI gates (RNF-5). This resolves the CI flakiness in finding 1.9.
- **D5 (1.2) – counting:** an accepted request counts toward its limits whatever its processing result. A write rejected by the general limit is a 429, so it doesn't count toward the write limit.
- **D6 (1.3) – `X-Forwarded-For` parsing:**
  - several lines are joined in order;
  - entries are trimmed and ports removed;
  - N counts from the right starting at 1;
  - zone IDs and invalid entries fall back to the connection address;
  - a request with no address at all goes to the "unknown" client.
- **D7 (1.13) – classification:** writes are classified by HTTP method alone, before processing. Liveness is the exact path `/health`, with any method.
- **D8 (1.10) – memory:** a hard maximum on tracked clients (default 10,000). The least recently active client is dropped first, and empty windows are dropped. The bound is now a number.
- **D9 (1.12) – exact messages:** the 429 and per-client 409 texts are fixed.
- **D10 (1.14) – privacy:** no client address is persisted or written to the application's own logs. Server access logs are a deployment choice, and the documentation says so.
- **D11 (2.1) – hop count:** belongs to this spec, with valid range 0 to 10, so a default of 0 is valid.
- **D12 (3.2) – concurrency:** counters, attribution and the per-client cap are concurrency-safe (RNF-3).

## Open questions
- (none)
