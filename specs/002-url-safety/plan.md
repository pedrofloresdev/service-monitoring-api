# Plan - 002 URL safety

Spec: `specs/002-url-safety/spec.md` (approved 2026-11-12). Status: approved 2026-11-12 (owner approved the plan including approvals 1-3: check path rewrite, committed self-signed test TLS key pair, no new dependency).

## Measurements (2026-11-12, throwaway probes in the session scratchpad, no project file edited)
Environment: project `.venv` (Python 3.11, FastAPI 0.135.1, pydantic 2.12.5, httpx 0.28.1, httpcore 1.0.9, certifi present as an httpx dependency). `cryptography` and `trustme` are not installed. The `openssl` CLI is available locally.

| # | Probe | Result | Consequence |
|---|---|---|---|
| M1 | pydantic `HttpUrl` normalization | `LOCALHOST.` -> host `localhost.`; `localhost..` kept as `localhost..`; `2130706433`, `0x7f000001`, `127.1` -> `127.0.0.1`; `0` -> `0.0.0.0`; `[::ffff:127.0.0.1]` -> host `[::ffff:7f00:1]` (with brackets); full-width `ｌｏｃａｌｈｏｓｔ` -> `localhost`; `bücher.de` -> `xn--bcher-kva.de`; default port dropped | The schema can classify the normalized host without any extra parsing of IPv4 notations. IPv6 hosts must have their brackets stripped. |
| M2 | Python `ipaddress.is_global` vs the spec table | `::7f00:1` and `64:ff9b::a00:1` report `is_global=True`, and so does `192.0.0.9`. `2002:a00:1::` and `2001::1` report False | `is_global` cannot implement the spec (embedded-IPv4 forms and the "whole block" rule). An explicit network table is needed. |
| M3 | Custom validator error through FastAPI | `PydanticCustomError("url_not_allowed", MSG)` in a field validator produces `{"type":"url_not_allowed","loc":["body","url"],"msg":MSG,"input":<raw submitted string>}` with no `ctx` and no `Value error,` prefix, listed together with other field errors, before the handler runs (so before any 404) | RF-2 and RF-5 can be met in request validation. |
| M4 | 422 raised after the handler started | `RequestValidationError([{type, loc, msg, input}])` produces the same JSON shape as M3. A 404 raised before it wins | RF-3 precedence (after 404, before 409) is achievable. A handler-level 422 has the same format. |
| M5 | Sync handler sleeping 2.0 s, concurrent `GET /health` | `/health` answered in 5.4-6.3 ms while the slow request ran on an AnyIO worker thread | Resolution inside a sync route handler does not block the event loop (RNF-4). |
| M6 | Pinned-IP HTTPS with httpx (local TLS server, self-signed cert for `monitor.test`, made with `openssl`) | `client.get("https://127.0.0.1:P/x", headers={"Host": "monitor.test:P"}, extensions={"sni_hostname": "monitor.test"})` -> 204; server saw SNI `monitor.test` and Host `monitor.test:P`. Wrong `sni_hostname` -> `ConnectError: certificate verify failed: Hostname mismatch`. No `sni_hostname` -> `ConnectError: ... IP address mismatch, certificate is not valid for '127.0.0.1'` | RF-10/RF-11 need **no new dependency**. httpx error texts can contain the pinned IP, so redaction is required (RF-15). |
| M7 | Proxies and CA | With `HTTPS_PROXY` set, a default `httpx.Client` went to the proxy (ConnectTimeout). `httpx.Client(trust_env=False, verify=httpx.create_ssl_context(trust_env=True))` ignored the proxy and trusted the cert from `SSL_CERT_FILE` | RF-14 met with httpx public API only. |
| M8 | Per-request connect timeout | `client.get(url, timeout=httpx.Timeout(10, connect=0.5))` on a blackholed address -> `ConnectTimeout('timed out')` after 0.51 s | The connect budget can be split across attempts per request (RF-10, RNF-2). |
| M9 | Exception chains | TCP refused: `ConnectError <- ConnectError <- ConnectionRefusedError`. TLS verify failure: `ConnectError <- ConnectError <- SSLCertVerificationError` | TLS failures can be told apart from TCP failures by walking `__cause__`/`__context__`. |
| M10 | `socket.getaddrinfo` per family in daemon threads | `localhost` -> v4 `127.0.0.1`, v6 `::1` in 8 ms; unknown name -> `gaierror` in 27 ms per family. (The `example.com` probe line did a real DNS lookup; no other network access.) | Two lookups (AF_INET, AF_INET6) with a shared deadline give partial answers and a hard bound (RF-3, RF-6). |
| M11 | `httpx.URL` host forms | `[::1]` -> `.host` `::1`; `xn--bcher-kva.de` -> `.host` `bücher.de` (Unicode); `localhost..` kept | Check time must use `raw_host` (ASCII/punycode) so the host matches the schema-time form and SNI gets ASCII. |

## Overview
One new module holds all URL-safety logic: pure classification functions plus one resolver with a deadline. Three callers use it:
1. **Request validation** (schemas): reserved names and literal IPs, no DNS (RF-2).
2. **Create/update handlers**: hostname resolution after the 404 check and before the 409 check (RF-3, RF-4), raising a domain error that `main.py` turns into a 422 (RF-5).
3. **The check** (`check_service`): determine addresses, block or fail, then connect only to validated addresses with a shared connect budget, original hostname for Host/SNI/certificate, no proxies, no redirects, redacted errors (RF-6 to RF-15).

No schema change, no migration, no new dependency, no Docker change, no scheduler lifecycle change.

## Files and responsibilities

| File | Change | Responsibility | RFs |
|---|---|---|---|
| `app/services/url_safety.py` | new | Blocked range table, embedded-IPv4 rule, reserved names, host classification, resolver with deadline, redaction, registration check, `UnsafeDestinationError`, the fixed messages | RF-2, RF-3, RF-4, RF-6 to RF-9, RF-15 |
| `app/services/monitor.py` | modify `check_service` only | Check algorithm: classify, resolve, block/fail, pinned send with fallback and connect budget, Host/SNI, no proxy, CA from env, no redirects, redaction. `check_service_by_id` and `get_consecutive_failures` unchanged | RF-6 to RF-15, RNF-1, RNF-2, RNF-5 |
| `app/schemas/services.py` | modify | `url` field validator (no DNS) on `ServiceCreate` and `ServiceUpdate`; keep the raw submitted URL for the handler-level 422 `input`; `description` on the `url` field | RF-1, RF-2, RF-5, RF-17 |
| `app/api/v1/services.py` | modify `create_service`, `update_service` | Call the registration check at the right point (create: before the duplicate query; update: after the 404, only when `url` is in the request). Nothing is written or scheduled before the check passes | RF-1, RF-3, RF-4, RF-5 |
| `app/main.py` | modify | Exception handler `UnsafeDestinationError -> 422` in the request-validation format | RF-5 |
| `app/core/config.py` | modify | `RESOLUTION_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0, le=30)` | RNF-3, definition "Resolution timeout" |
| `.env.example` | modify | Document the new setting | RF-17, RNF-3 |
| `docs/SPEC.md` | modify | Current scope: URL safety rules in short; roadmap already renumbered at approval | RF-17 |
| `docs/architecture.md` | modify | Component table (new module), check sequence diagram (resolve -> validate -> pinned connect), URL safety section: table, embedded forms, reserved names, errors, proxy/CA behavior, setting | RF-17 |
| `tests/conftest.py` | modify | Two autouse fixtures: network guard (RNF-7) and a fake resolver returning a public address for every hostname (keeps existing tests offline, RNF-8) | RNF-7, RNF-8 |
| `tests/test_url_safety.py` | new | Pure logic table tests, resolver tests with an injected `getaddrinfo`, redaction, settings validation | RNF-6 (rejected/accepted lists, configuration) |
| `tests/test_services.py` | modify (add tests only) | Create/update rejected, accepted and precedence cases, error shape, no write/no schedule on rejection, RNF-4 responsiveness | RF-1 to RF-5, RNF-4, RNF-6 |
| `tests/test_monitor.py` | modify (add tests only) | Check-time cases, fallback, connect budget, redirect/proxy/TLS on a loopback server, redaction, existing unsafe service | RF-6 to RF-16, RNF-1, RNF-2, RNF-6 |
| `tests/fixtures/tls/monitor-test.crt`, `monitor-test.key` | new | Self-signed test certificate for `monitor.test` (valid 100 years), used only by the loopback TLS test. Regeneration command in a README next to it | RF-11, RF-14 (needs approval, see below) |

## Pure logic functions (`app/services/url_safety.py`)
All pure functions are written test-first (constitution P4).

```python
UNSAFE_LITERAL_MESSAGE  = "unsafe destination: host is not a public address"         # RF-7
UNSAFE_RESOLVED_MESSAGE = "unsafe destination: host resolves to a non-public address" # RF-8
NOT_ALLOWED_MESSAGE     = "Destination not allowed: the URL host is not a public address"  # RF-5
RESOLUTION_FAILED_PREFIX = "resolution failed:"                                        # RF-9

BLOCKED_IPV4: tuple[IPv4Network, ...]   # the 15 rows of the spec table
GLOBAL_UNICAST_V6 = ip_network("2000::/3")
BLOCKED_IPV6_IN_GLOBAL: tuple[IPv6Network, ...]  # 2001::/23, 2001:db8::/32, 2002::/16, 3fff::/20, 5f00::/16
IPV4_MAPPED = ip_network("::ffff:0:0/96"); IPV4_COMPAT = ip_network("::/96"); NAT64_WKP = ip_network("64:ff9b::/96")

def embedded_ipv4(ip: IPv6Address) -> IPv4Address | None
    # mapped, compatible (except :: and ::1), well-known NAT64 -> last 32 bits; else None
def is_public_address(ip: IPv4Address | IPv6Address) -> bool
    # v4: not in BLOCKED_IPV4
    # v6: if embedded_ipv4(ip): judge that v4
    #     elif ip not in 2000::/3: False
    #     else: not in BLOCKED_IPV6_IN_GLOBAL
def normalize_host(host: str) -> str
    # strip one pair of [] brackets, lower-case. Input is already parser-normalized (M1, M11).
def is_reserved_name(host: str) -> bool
    # h = host.rstrip("."); h == "localhost" or h.endswith(".localhost")
def parse_literal(host: str) -> IPv4Address | IPv6Address | None
def classify_host(host: str) -> HostClass
    # RESERVED | UNSAFE_LITERAL | PUBLIC_LITERAL | HOSTNAME   (no I/O)
def first_unsafe(addresses: Sequence[IPAddress]) -> bool   # any non-public
def redact(message: str, addresses: Sequence[str], host: str) -> str
    # replace every address string (plain and [bracketed]) by host (RF-15)
```

Impure, small, injectable:

```python
@dataclass(frozen=True)
class Resolution:
    addresses: tuple[IPAddress, ...]   # IPv4 first, then IPv6, duplicates removed, order kept
    error: str | None                  # set only when addresses is empty: "timed out" | "name not found" | other short reason

def resolve_host(host: str, timeout: float, getaddrinfo: GetAddrInfo = socket.getaddrinfo) -> Resolution

def ensure_registration_allowed(url: HttpUrl, submitted: str) -> None
    # classify; for HOSTNAME resolve with settings.RESOLUTION_TIMEOUT_SECONDS;
    # any non-public -> raise UnsafeDestinationError(submitted); no address -> return (RF-4)

class UnsafeDestinationError(Exception): submitted_url: str
```

## Config and state changes
| Setting | Type | Default | Bounds | Notes |
|---|---|---|---|---|
| `RESOLUTION_TIMEOUT_SECONDS` | float | 2.0 | `> 0`, `<= 30` | New. Invalid value -> pydantic-settings `ValidationError` when `Settings()` is built at import, so the app does not start (RNF-3). Documented in `.env.example`. |
| `CHECK_TIMEOUT_SECONDS` | int | 10 | unchanged | Now also the shared connect budget (RF-10). Per-read/write timeout unchanged (RF-12). |

No database, migration, response-schema or scheduler-job change (RNF-9). OpenAPI changes only by the new `description` on the `url` field, which RF-17 requires. No state is kept between checks: every check resolves again (RF-6). Existing rows are never touched (RF-16).

## Algorithms

### A1. Resolver with deadline (RF-3, RF-6, RF-9; M10)
```
resolve_host(host, timeout, getaddrinfo):
    results = Queue()
    for family in (AF_INET, AF_INET6):
        start daemon thread: try results.put((family, [ip(ai[4][0]) for ai in getaddrinfo(host, None, family, SOCK_STREAM)]))
                             except OSError as e: results.put((family, e))
    deadline = monotonic() + timeout; answers = {}
    while len(answers) < 2 and (remaining := deadline - monotonic()) > 0:
        try: family, value = results.get(timeout=remaining); answers[family] = value
        except Empty: break
    addresses = dedupe(v4 list if ok) + dedupe(v6 list if ok)   # partial answers kept
    if addresses: return Resolution(addresses, None)
    error = "timed out" if any family missing from answers else short reason of the first gaierror
    return Resolution((), error)
```
A lookup that outlives the deadline keeps its daemon thread until the OS resolver gives up; its result is discarded. See decision D3.

### A2. Request validation (RF-1, RF-2, RF-5; M1, M3)
```
field_validator("url") on ServiceCreate and ServiceUpdate (after HttpUrl parsing, None skipped):
    cls = classify_host(normalize_host(url.host))
    if cls in (RESERVED, UNSAFE_LITERAL): raise PydanticCustomError("url_not_allowed", NOT_ALLOWED_MESSAGE)
    return url
model_validator(mode="wrap"): keep the raw "url" input in a private attribute _submitted_url
```
Because this runs in request validation, its 422 lists other field errors too and comes before any 404 (M3).

### A3. Create and update handlers (RF-1, RF-3, RF-4, RF-5)
```
create_service(payload):
    ensure_registration_allowed(payload.url, payload.submitted_url)   # may raise -> 422, before 409
    duplicate check -> 409
    insert, commit, schedule

update_service(id, payload):
    load service -> 404 if missing
    updates = payload.model_dump(exclude_unset=True)
    if "url" in updates: ensure_registration_allowed(payload.url, payload.submitted_url)   # even if equal to stored (RF-1)
    apply, commit, reschedule (unchanged)

main.py: exception_handler(UnsafeDestinationError) ->
    JSONResponse(422, {"detail": [{"type": "url_not_allowed", "loc": ["body", "url"],
                                   "msg": NOT_ALLOWED_MESSAGE, "input": exc.submitted_url}]})
```
Handlers are sync, so resolution runs on a worker thread and never on the event loop (M5, RNF-4). Since the check raises before any write, a rejected request changes and reschedules nothing (RF-5).

### A4. Check (RF-6 to RF-15, RNF-1, RNF-2; M6-M9, M11)
```
check_service(service):
    start = monotonic()                                   # response time includes resolution (RNF-1)
    url = httpx.URL(service.url); host = normalize_host(url.raw_host.decode("ascii"))
    try:
        match classify_host(host):
            RESERVED | UNSAFE_LITERAL -> return failed(UNSAFE_LITERAL_MESSAGE)        # RF-7, no request
            PUBLIC_LITERAL            -> addresses, pinned = (parse_literal(host),), False
            HOSTNAME:
                r = resolve_host(host, settings.RESOLUTION_TIMEOUT_SECONDS)
                if not r.addresses: return failed(f"resolution failed: {host}: {r.error}")   # RF-9
                if first_unsafe(r.addresses): return failed(UNSAFE_RESOLVED_MESSAGE)        # RF-8
                addresses, pinned = r.addresses, True
        host_header = httpx.Request("GET", url).headers["Host"]     # same Host httpx would send today
        verify = httpx.create_ssl_context(trust_env=True)           # SSL_CERT_FILE / SSL_CERT_DIR honored (RF-14)
        budget = settings.CHECK_TIMEOUT_SECONDS; last_error = None
        with httpx.Client(trust_env=False, verify=verify, follow_redirects=False,      # RF-13, RF-14
                          timeout=settings.CHECK_TIMEOUT_SECONDS) as client:
            for addr in addresses:                                   # IPv4 first, then IPv6 (D5)
                if budget <= 0: break
                t0 = monotonic()
                target = url.copy_with(host=str(addr)) if pinned else url
                extensions = {"sni_hostname": host} if pinned and url.scheme == "https" else {}
                try:
                    response = client.get(target, headers={"Host": host_header}, extensions=extensions,
                                          timeout=httpx.Timeout(settings.CHECK_TIMEOUT_SECONDS, connect=budget))  # RF-10, RF-12
                except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                    if caused_by_tls(exc): raise                     # TLS failure: no fallback, as today (M9)
                    last_error = exc; budget -= monotonic() - t0; continue
                return result_from(response, start)                  # UP/DOWN exactly as today
        raise last_error or httpx.ConnectTimeout("connect budget exhausted")
    except Exception as exc:
        return failed(redact(str(exc), [str(a) for a in addresses_seen], host)[:500])  # RF-15
```
`failed(msg)` = `{"status": "DOWN", "status_code": None, "response_time_ms": None, "error_message": msg}`, the same shape as today's failure path. `check_service_by_id` is unchanged, so blocked and failed checks are stored and logged as today, and exceptions never reach the scheduler (RNF-5).

Worst-case duration (RNF-2): resolution timeout + connect budget + per-read/write timeout. Three blackholed addresses consume one shared budget, not three.

## Rendering / UI behavior
No UI. Observable API behavior:
- `POST /api/v1/services`, `PATCH /api/v1/services/{id}`: new 422 cases, in the existing `detail` list format (M3, M4).
- `GET /api/v1/metrics/services/{id}`: blocked and resolution-failed checks appear as DOWN with the fixed messages.
- `GET /api/v1/status`: unchanged rules; such services become DOWN after `FAIL_THRESHOLD` failures.
- OpenAPI: only the `url` field description changes (RF-17).

## Decisions

| # | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | Explicit network table in the code, mirrored from the spec | `ipaddress.is_global` / `is_private` | M2: `is_global` disagrees with the spec on `::7f00:1`, NAT64, `192.0.0.9`. The table is the spec's closed list and is tested row by row. |
| D2 | Pin the connection by rewriting the URL host to the validated IP and passing `Host` header + `sni_hostname` extension | Custom httpx transport / custom resolver hook; a new dependency for DNS-pinned HTTP | M6/M7: public httpx API is enough, no new dependency, certificate verification stays the library default and is checked against the hostname. |
| D3 | `socket.getaddrinfo` per address family in daemon threads with a shared deadline | `dnspython` (new dependency, ignores the hosts file and container DNS the spec says to judge); one `AF_UNSPEC` call in a thread (no partial answers); a bounded thread pool (needs a pool-size tunable the spec does not define, and a full pool turns every lookup into "timed out") | Honors every name source the OS uses, gives partial answers, has a hard bound. Cost: a lookup that hangs keeps one daemon thread until the OS resolver gives up (bounded by the system resolver timeout); the thread never blocks shutdown. |
| D4 | DNS check of create/update in the sync handler via the service layer, domain error turned into 422 by an exception handler | DNS lookup in the pydantic validator (runs before 404 and, per pass-2 review, on the event-loop thread); raising `RequestValidationError` from the service layer (FastAPI type leaking into business logic) | Matches the spec precedence (M4), keeps the event loop free (M5), keeps `app/services` free of FastAPI imports. |
| D5 | Address order: IPv4 first, then IPv6, each in resolver order | System `AF_UNSPEC` order (often IPv6 first) | With one shared budget, trying the family that is reachable almost everywhere first minimizes the risk that a blackholed IPv6 path eats the budget, which protects RNF-1. The spec prescribes no order. |
| D6 | Fall back only on TCP-level connect errors and connect timeouts; TLS errors end the check | Fall back on every `ConnectError` | Today, httpcore retries addresses only for the TCP connect; a certificate error does not try another address. Keeping that keeps outcomes equal (RNF-1). Detected through the exception chain (M9). |
| D7 | Build the SSL context per check with `httpx.create_ssl_context(trust_env=True)` and pass it to a `trust_env=False` client | Module-level cached context; `ssl.create_default_context()` | Same per-check cost and same CA source (certifi, or `SSL_CERT_FILE`/`SSL_CERT_DIR`) as today's `verify=True` default (M7). The stdlib default context would switch to the system store and could change outcomes. |
| D8 | Keep the raw submitted URL via a wrap model validator for the handler-level 422 `input` | Use the normalized URL as `input` | RF-5 says "input equal to the submitted URL"; the schema-level 422 already echoes the raw input (M3), so both 422 kinds match. |
| D9 | Redact every resolved address from error text, instead of choosing which httpx messages are safe | Whitelist of exception types with fixed messages | M6 shows httpx/ssl messages can contain the pinned IP; redaction covers every path, including future library messages (RF-15). |
| D10 | Real loopback servers for redirect, proxy and TLS tests, with a committed self-signed test certificate | Mock-only tests; generate the cert at test time (needs `openssl` binary in CI, or `cryptography`/`trustme` as new dependencies) | Only a real TLS handshake proves SNI and hostname verification (RF-11) and CA-from-env (RF-14). A committed throwaway cert keeps CI hermetic with no new dependency. |

## Test strategy
Default suite stays offline (RNF-7). Tests first for every pure function and for each behavior (P4).

**Harness (`tests/conftest.py`)**
- `network_guard` (autouse, skipped for tests marked `postgres`): `monkeypatch` `socket.getaddrinfo` to raise unless the host is a loopback literal (`127.0.0.1`, `::1`) or `None`, and `socket.socket.connect`/`connect_ex` to raise unless the address is loopback. One test asserts the guard rejects `getaddrinfo("example.com")` and a connect to `198.51.100.1`. TestClient uses no sockets. The existing `127.0.0.1:1` schema-check test stays allowed.
- `fake_resolver` (autouse): patches `url_safety.resolve_host` to return a fixed public address (`93.184.216.34`) for any hostname. Tests override it per case (private answer, mixed answer, partial answer, no answer, sleep).
- Existing tests: no assertion changes (RNF-8). They keep mocking `httpx.Client`; `check_service` still constructs `httpx.Client(...)` and calls `.get(...)` on the context-managed client, so the mocks still apply. With one fake address, the `"Connection refused"` test still sees that text after the single attempt.

**Automated (RNF-6 coverage map)**
| Area | File | Cases |
|---|---|---|
| Table + embedded forms + reserved names + classification | `test_url_safety.py` | Parametrized over every table row (one inside, one just outside where meaningful), mapped/compatible/NAT64 of private and public IPv4, `::`/`::1`, 6to4, Teredo, `64:ff9b:1::1`, `localhost` / `LOCALHOST.` / `localhost..` / `app.localhost`, `localhost.example.com` (not reserved) |
| Resolver | `test_url_safety.py` | Injected `getaddrinfo`: both families, IPv4 only + IPv6 error (partial), IPv6 sleeps past the deadline (partial, returns by deadline + tolerance), both sleep (timed out), both `gaierror` (name not found), duplicate addresses removed, IPv4-first order |
| Redaction | `test_url_safety.py` | IPv4, IPv6, bracketed IPv6 in a message |
| Settings | `test_url_safety.py` | `Settings(RESOLUTION_TIMEOUT_SECONDS=...)` with `"abc"`, `0`, `-1`, `30.5` -> `ValidationError`; `0.1` and `30` accepted; default 2.0 |
| Create/update rejected + accepted | `test_services.py` | The spec's two lists through `POST` and `PATCH`; exact 422 body (type, loc, msg, input) for both 422 kinds; no row created/changed, `schedule_service` not called |
| Precedence | `test_services.py` | reserved URL + unknown id -> 422; resolves-private + unknown id -> 404; resolves-private duplicate on create -> 422; reserved URL + empty name -> one 422 with two entries; `PATCH` with the stored unsafe URL + name change -> 422; `PATCH` without `url` on an unsafe service -> 200 |
| RNF-4 | `test_services.py` | Resolution timeout set to 1.0 s; fake resolver sleeps 1.0 s in a create sent from a thread; `GET /health` and `GET /api/v1/status` each < 500 ms |
| Check-time decisions | `test_monitor.py` | RF-7 (`localhost` variants, unsafe literal), RF-8 (private, mixed), RF-9 (no address, timed out) assert exact messages, DOWN, no status code, no response time, and that `httpx.Client` was never called; public-then-private resolution across two checks |
| Fallback + budget | `test_monitor.py` | Mocked client: first address `ConnectError` refused, second 200 -> UP; TLS-caused `ConnectError` -> no second attempt; 3 addresses each raising `ConnectTimeout` after sleeping their `connect` timeout, `CHECK_TIMEOUT_SECONDS=1` -> one `get` per address at most, total < resolution timeout + 1 s + 1 s tolerance (RNF-2); pinned target URL, `Host` header and `sni_hostname` passed |
| Loopback real servers | `test_monitor.py` | Fake resolver maps `monitor.test` -> `127.0.0.1` and the test patches `is_public_address` to accept it (test seam, documented). (a) server answers 302 to `http://127.0.0.1/secret`: result status code 302, server got exactly one request (RF-13). (b) `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` set to an unreachable address: check still reaches the server (RF-14). (c) TLS server with the fixture cert, `SSL_CERT_FILE` pointing to it: UP, server saw SNI and Host `monitor.test` (RF-11, RF-14); same with a resolver name the cert does not cover -> DOWN with a hostname-mismatch error that contains no IP (RF-15) |
| Existing unsafe service | `test_monitor.py` | Service row with `http://localhost/` created directly in the DB, `check_service_by_id` -> stored DOWN metric with RF-7 message; service row and earlier metrics unchanged (RF-16) |

**Manual (in `validation.md`)**
- `docker compose up --build`; `POST` `http://169.254.169.254/` and `http://db:5432/` -> 422; `POST` `https://example.com/` -> 201 and, after one interval, an UP metric with a status code (RF-11 on a real public HTTPS site, RNF-1); `GET /health`, `GET /api/v1/status` respond.
- Run the default suite once with networking disabled at the OS level (`unshare -rn pytest` on Linux) as an extra check of RNF-7.
- OpenAPI diff against `master`: only the `url` description changed.

**Quality gates per task (RNF-10):** `ruff check .`, `ruff format --check .`, `mypy`, `pytest` (coverage floor 88, no regression), and since persistence is not touched, the PostgreSQL checks run once at the end (`python -m tools.schema_check`, `pytest -m postgres --no-cov`).

## RF coverage
| Req | Covered by |
|---|---|
| RF-1 | A2, A3 (`"url" in updates`), tests in `test_services.py` |
| RF-2 | A2, `classify_host`, M3 |
| RF-3 | A3, A1, `ensure_registration_allowed`, D4 |
| RF-4 | `ensure_registration_allowed` returns on empty resolution |
| RF-5 | A2 + `main.py` handler, D8, exact-body tests |
| RF-6 | A4 classify/resolve, A1 |
| RF-7 / RF-8 / RF-9 | A4 early returns with fixed messages |
| RF-10 | A4 loop, D5, D6, M8 |
| RF-11 | A4 Host + `sni_hostname`, D2, loopback TLS test |
| RF-12 | A4 `httpx.Timeout(CHECK_TIMEOUT_SECONDS, connect=budget)` |
| RF-13 | `follow_redirects=False`, loopback redirect test |
| RF-14 | `trust_env=False` + `create_ssl_context(trust_env=True)`, D7, loopback proxy and TLS tests |
| RF-15 | `redact`, D9, no-IP assertions |
| RF-16 | No data-changing code; existing-unsafe-service test |
| RF-17 | `docs/SPEC.md`, `docs/architecture.md`, `.env.example`, `url` description |
| RNF-1 | D5, D6, D7, unchanged UP/DOWN logic, timing start kept, manual public HTTPS check |
| RNF-2 | Shared budget, budget test |
| RNF-3 | `Field(gt=0, le=30)`, settings tests |
| RNF-4 | D4, M5, responsiveness test |
| RNF-5 | `check_service_by_id` unchanged, `check_service` catches everything |
| RNF-6 | Test strategy table |
| RNF-7 | `network_guard`, manual `unshare -rn` run |
| RNF-8 | Existing tests keep their assertions; only conftest fixtures added |
| RNF-9 | No schema/response change; OpenAPI diff |
| RNF-10 | Quality gates |
| RNF-11 | No new dependency (D2, D3, D10) |

## Approvals needed
1. **Check path change (sensitive area: monitoring/scheduler jobs).** `check_service` is rewritten as in A4. The scheduler lifecycle and job wiring are not touched.
2. **Committed test TLS key pair** (`tests/fixtures/tls/`): a self-signed certificate and private key for `monitor.test`, used only by tests. It is not a secret, but secret scanners may flag it. Alternative if refused: drop the real TLS test and verify RF-11/RF-14 with mocks plus the manual compose check (weaker evidence).
3. **No new dependency** (RNF-11 satisfied): only the standard library, httpx public API and pydantic are used. Confirm.
4. No schema/migration change and no Docker/compose change: the new setting has a default, so compose needs nothing.

## Task estimate
About 10 tasks: (1) test harness guard + fake resolver, (2) setting, (3) classification table, (4) resolver, (5) schema validator + error shape, (6) handler DNS check + precedence + RNF-4, (7) check decisions RF-6-RF-9, (8) pinned send + fallback + budget + redaction, (9) loopback redirect/proxy/TLS tests + fixture cert, (10) docs + manual compose + validation. That is at the 10-task limit, so no split is proposed unless `/sdd-tasks` finds a task over 30 minutes.
