# 002 - URL safety

Status: approved (2026-11-12, after two `/sdd-clarify` passes)
Change (2026-11-23, owner-approved via /sdd-change after /sdd-validate): RF-17 keeps the detailed URL safety reference only in `docs/architecture.md`; `docs/SPEC.md` summarizes and links to it, so the two cannot drift.

## Context and goal
A service URL is accepted if it is a well-formed `http` or `https` URL. Nothing checks where it points. Anyone who can register or update a service can make the scheduler send requests to:
- the API host itself (`localhost`, `127.0.0.1`, `[::1]`),
- private networks (for example `10.0.0.0/8`, `192.168.0.0/16`, IPv6 unique-local),
- link-local addresses, including the link-local cloud metadata endpoint `169.254.169.254`, which can expose credentials,
- other hosts on the deployment network (for example the database container).

The stored checks (status code, response time, error text) then show what answers at those addresses. This is server-side request forgery (SSRF). It conflicts with constitution principle 7 ("Secure by default"; validate all input).

A hostname is also a risk. It can resolve to a private address, and its DNS answer can change after registration (DNS rebinding). So checking the URL only once, at registration, is not enough.

Goal: the system never sends a check request to a non-public address. Unsafe URLs are rejected when a service is created or its URL is updated. Every scheduled check validates the destination again and records a blocked check as a failed check with a clear reason. Checks of public URLs give the same results as before, except for the cases listed in RNF-1. Existing health data is never altered.

Roadmap: this spec is inserted as 002. The later roadmap specs each move down one number (metrics-retention 003, alerting 004, postgres-test-parity 005, api-auth 006); see RF-16.

## Users
- **Owner/operator:** wants to run the API, possibly in the cloud, without it being usable to probe their own host, internal network or link-local cloud metadata.
- **API client registering services:** wants a clear error when a URL is not allowed, and wants to see why a service is DOWN when its destination is blocked.
- **Reviewer:** wants to see that the portfolio project handles a known OWASP risk.

## User stories
- As an API client, when I register `http://169.254.169.254/latest/meta-data/`, the request is rejected with a validation error on the `url` field, and no service is created.
- As an API client, when I change an existing service's URL to `http://localhost:8000/`, the update is rejected and the service keeps its previous URL and schedule.
- As an operator, if a registered hostname later starts resolving to a private address, the next check is not sent. A failed check with an "unsafe destination" error is stored. The status endpoint shows the service DOWN (once the failure threshold is reached), and the service's metrics show the reason.
- As an operator upgrading an existing deployment, my services that already point at unsafe destinations stay registered with their history. Their new checks are blocked and recorded as failures until I fix or delete them.
- As an operator monitoring public HTTPS sites, my checks keep returning the same status codes and UP/DOWN results as before this change.
- As an operator, while someone registers a service whose DNS is slow, the rest of the API keeps answering promptly.

## Definitions
- **Normalized host:** the URL's host after the URL is parsed and normalized (lower case, IDNA, percent-decoding, IPv4 notations rewritten to dotted form). Every host rule in this spec applies to the normalized host.
- **Blocked range table:** the closed list of ranges below. It is derived from the IANA IPv4 and IPv6 Special-Purpose Address Registries (entries with "Globally Reachable = False", as of 2026-11-12). Entries marked * are additions beyond the registry. A whole block is blocked even where the registry marks a smaller sub-block as globally reachable.

  | IPv4 range | Meaning |
  |---|---|
  | `0.0.0.0/8` | "This network", includes `0.0.0.0` |
  | `10.0.0.0/8` | Private |
  | `100.64.0.0/10` | Shared address space (carrier-grade NAT) |
  | `127.0.0.0/8` | Loopback |
  | `169.254.0.0/16` | Link-local, includes cloud metadata `169.254.169.254` |
  | `172.16.0.0/12` | Private |
  | `192.0.0.0/24` | IETF protocol assignments |
  | `192.0.2.0/24` | Documentation (TEST-NET-1) |
  | `192.88.99.0/24` | Deprecated 6to4 relay anycast * |
  | `192.168.0.0/16` | Private |
  | `198.18.0.0/15` | Benchmarking |
  | `198.51.100.0/24` | Documentation (TEST-NET-2) |
  | `203.0.113.0/24` | Documentation (TEST-NET-3) |
  | `224.0.0.0/4` | Multicast * |
  | `240.0.0.0/4` | Reserved, includes broadcast `255.255.255.255` |

  | IPv6 range | Meaning |
  |---|---|
  | everything outside `2000::/3` * | Not global unicast. Includes `::`, `::1`, local-use NAT64 `64:ff9b:1::/48`, discard `100::/64`, unique-local `fc00::/7`, link-local `fe80::/10`, site-local `fec0::/10`, multicast `ff00::/8`. Exceptions: IPv4-mapped, IPv4-compatible and well-known NAT64 addresses, see below |
  | `2001::/23` | IETF protocol assignments, includes Teredo `2001::/32` |
  | `2001:db8::/32` | Documentation |
  | `2002::/16` | 6to4 * |
  | `3fff::/20` | Documentation |
  | `5f00::/16` | Segment routing (SRv6) SIDs |

- **Embedded-IPv4 forms:** these IPv6 addresses are judged by the IPv4 address in their last 32 bits, instead of by the IPv6 table:
  - IPv4-mapped `::ffff:0:0/96`,
  - IPv4-compatible `::/96`, except `::` and `::1` (which are blocked as outside `2000::/3`),
  - well-known NAT64 `64:ff9b::/96` *.
- **Non-public address:** an address inside the blocked range table, or an embedded-IPv4 form whose embedded IPv4 address is inside the IPv4 table.
- **Public address:** any address that is not a non-public address.
- **Reserved name:** a normalized host that, after removing all trailing dots, is `localhost` or ends in `.localhost`.
- **Literal host:** a normalized host that is an IPv4 address or a bracketed IPv6 address.
- **Unsafe URL:** a URL whose normalized host is a reserved name, a literal host that is a non-public address, or a hostname that resolves to at least one non-public address.
- **Resolution timeout:** a new setting. A decimal number of seconds, greater than 0 and at most 30, default 2. It bounds the whole name resolution of one host (all address families together).
- **Validated address:** a public address obtained in the current check, from resolving the host or from the literal host itself.
- **Connect budget:** the existing check timeout, shared by all connection attempts of one check.
- **Blocked check:** a scheduled check where no request is sent because the destination is unsafe at check time.

## Functional requirements
### Create and update
- RF-1: WHEN a request creates a service, or a request updates a service and includes the `url` field, THE SYSTEM SHALL validate the URL as in RF-2 and RF-3. This applies even when the URL equals the stored one.
- RF-2: IF the normalized host is a reserved name or a literal host that is a non-public address, THEN THE SYSTEM SHALL reject the request with HTTP 422 as part of request validation, without any DNS lookup. Errors for other invalid fields of the same request SHALL be listed in the same response. This 422 takes precedence over 404 (unknown service).
- RF-3: WHEN the normalized host is a hostname that is not a reserved name, and the request has passed request validation and, for an update, the service exists, THE SYSTEM SHALL resolve the hostname within the resolution timeout. IF any address returned within the timeout is a non-public address, THEN THE SYSTEM SHALL reject the request with HTTP 422. This 422 takes precedence over 409 (duplicate URL on create).
- RF-4: IF no address is returned within the resolution timeout (unknown name, DNS error or timeout), THEN THE SYSTEM SHALL accept the URL. The destination is validated again at every check (RF-6).
- RF-5: WHEN a request is rejected under RF-2 or RF-3, THE SYSTEM SHALL respond in the existing request-validation error format: a `detail` list whose URL entry has location `["body", "url"]`, type `url_not_allowed`, message exactly `Destination not allowed: the URL host is not a public address`, and input equal to the submitted URL. THE SYSTEM SHALL NOT create, change or reschedule any service.

### Checks
- RF-6: WHEN a scheduled check runs, THE SYSTEM SHALL, before sending any request, determine the destination addresses: reject a reserved name, judge a literal host, or resolve a hostname within the resolution timeout and judge the addresses returned within it.
- RF-7: IF at check time the normalized host is a reserved name or a literal host that is a non-public address, THEN THE SYSTEM SHALL NOT send the request, and SHALL store a failed check: status DOWN, no status code, no response time, and error message exactly `unsafe destination: host is not a public address`.
- RF-8: IF at check time any address resolved for a hostname is a non-public address, THEN THE SYSTEM SHALL NOT send the request, and SHALL store a failed check: status DOWN, no status code, no response time, and error message exactly `unsafe destination: host resolves to a non-public address`.
- RF-9: IF at check time the hostname returns no address within the resolution timeout, THEN THE SYSTEM SHALL NOT send the request, and SHALL store a failed check: status DOWN, no status code, no response time, and an error message that starts with `resolution failed:`.
- RF-10: WHEN a check sends its request, THE SYSTEM SHALL connect only to validated addresses of that same check, trying them until one connects. All attempts share the connect budget. Once the budget is spent, no further address is tried and the check fails as a connection timeout. THE SYSTEM SHALL NOT connect to an address obtained any other way.
- RF-11: WHEN a check connects to a validated address of a hostname, THE SYSTEM SHALL use the original hostname for the HTTP `Host` header, for TLS server name indication and for certificate verification. Certificate verification SHALL stay enabled.
- RF-12: WHILE a check reads the response, THE SYSTEM SHALL apply the existing check timeout to each read and write, as today.
- RF-13: THE SYSTEM SHALL NOT follow HTTP redirects during a check. The check result is the redirect's status code compared with the expected status.
- RF-14: THE SYSTEM SHALL ignore proxy settings and `.netrc` credentials from the environment when sending a check, and SHALL always connect directly. THE SYSTEM SHALL keep trusting custom CA certificates configured in the environment (`SSL_CERT_FILE`, `SSL_CERT_DIR`).

### Data and docs
- RF-15: THE SYSTEM SHALL NOT include any resolved IP address in a 422 response or in a stored check's error message. The hostname given by the client may appear.
- RF-16: THE SYSTEM SHALL NOT modify, deactivate or delete a service or its stored checks because its URL is unsafe. Such services stay active and scheduled, and their checks are handled by RF-6 to RF-9.
- RF-17: THE SYSTEM SHALL document the blocked range table, the embedded-IPv4 forms, the reserved names, the 422 error, the check error messages, the proxy behavior (RF-14) and the resolution timeout setting in the architecture document, which is the single detailed reference. The product SPEC SHALL summarize the URL safety rules and point to that section, and the API description of the `url` field SHALL state them in short form. The product SPEC roadmap SHALL show the new numbering (see Context).

## Non-functional requirements
- RNF-1: For a URL whose host resolves only to public addresses, a check gives the same outcome as before this spec: same status code, same UP/DOWN, same redirect handling, and the response time is still measured from the start of the check (including resolution). Constitution principle 1. The only exceptions:
  - a deployment that relies on an environment proxy for checks (RF-14),
  - a host whose name resolution takes longer than the resolution timeout (RF-9),
  - a host where connecting needs more than the connect budget across all its addresses (RF-10).
- RNF-2: A check is bounded in time: resolution timeout + connect budget + the existing per-read/write timeout (RF-12). With 3 unreachable validated addresses, the check fails after at most the resolution timeout plus the check timeout (plus 1 second of tolerance), not 3 times the check timeout. Constitution principle 6.
- RNF-3: An invalid resolution timeout value (not a number, 0 or less, or more than 30) stops the application at startup with a configuration error. The existing check timeout setting is unchanged.
- RNF-4: WHILE a create or update request is waiting on a name resolution that takes the full resolution timeout, `GET /health` and `GET /api/v1/status` SHALL each still respond in under 500 ms. Constitution principle 6.
- RNF-5: A blocked or unresolvable check never crashes the scheduler. Apart from RNF-2 and RNF-4, no new throughput or concurrency guarantee is required.
- RNF-6: Automated tests cover, at minimum:
  - **Rejected at create/update:** IPv4 and IPv6 loopback, each IPv4 private range, `169.254.169.254`, `0.0.0.0`, `100.64.0.1`, `240.0.0.1`, IPv4-mapped, IPv4-compatible and NAT64 forms of a private address, a 6to4 address, a Teredo address, `64:ff9b:1::1`, decimal and shortened IPv4 notations of `127.0.0.1`, `localhost` / `LOCALHOST.` / `localhost..` / `app.localhost` with DNS unavailable, a hostname that resolves to a mix of public and private addresses, a hostname whose resolution is a partial answer containing a private address.
  - **Accepted at create/update:** a public hostname, IPv4-mapped, IPv4-compatible and NAT64 forms of a public address, an unresolvable hostname, a hostname whose resolution times out.
  - **Precedence:** a reserved-name URL on an unknown service id (422), a resolving-to-private URL on an unknown service id (404), a resolving-to-private URL that duplicates an existing service on create (422), a reserved-name URL together with another invalid field (one 422 with both entries).
  - **At check time:** each RF-7 / RF-8 / RF-9 case including the `localhost` variants on an existing service, a hostname whose resolution changes from public to private between registration and check, a partial answer (IPv4 returned, IPv6 lookup fails), fallback from an unreachable validated address to a reachable one, the connect budget with 3 unreachable addresses (RNF-2), a redirect to a private address, a proxy set in the environment, an HTTPS check that keeps hostname verification, and absence of resolved IPs in error messages.
  - **Configuration:** each invalid resolution timeout value (RNF-3).
- RNF-7: The default test run makes no DNS lookups and no connections except to loopback addresses. This is verified by running the default suite with a guard that fails any DNS lookup or non-loopback connection.
- RNF-8: Existing tests may change their setup and test doubles, including how the outgoing request is simulated. The outcomes they assert (status, status code, stored check, meaning of the error) stay the same, except error texts that this spec replaces with fixed texts.
- RNF-9: No change to the database schema, to response shapes or to status/uptime rules, other than the new 422 cases on create/update.
- RNF-10: The spec 000/001 checks stay green: lint, format, strict type check, tests with the coverage floor (no regression from 88), schema check and PostgreSQL tests.
- RNF-11: Any new dependency needs owner approval in the plan phase.

## Edge cases
- URL with userinfo or an unusual port (`http://user@10.0.0.1:8443/`): the host decides. A non-public host is rejected whatever the port.
- `localhost`, `LOCALHOST.`, `localhost..` or `api.localhost` while DNS is down: rejected by name (RF-2), not accepted as unresolvable. Full-width or soft-hyphen spellings are covered because rules apply to the normalized host.
- Names from the host's own name sources (hosts file, container DNS such as the `db` service): judged by the address they resolve to. Private addresses are rejected. This is intended.
- CNAME chains: judged by the final addresses returned.
- Hostname with several addresses where only one is non-public: rejected at registration (RF-3) and blocked at check time (RF-8).
- Partial answer (IPv4 returned, IPv6 lookup fails or exceeds the timeout): the addresses returned within the timeout are judged. At check time only those are contacted (RF-10).
- IPv6-only deployment with DNS64: a public IPv4-only site returns a `64:ff9b::/96` address embedding its public IPv4. It is judged by that IPv4 and accepted.
- Hostname that resolves to public addresses at registration and to a private one later: accepted at registration, blocked at each check that sees the private address, and checked normally again once it resolves to public addresses only.
- Unresolvable hostname at registration: accepted (RF-4). Each check records a `resolution failed:` error until it resolves.
- First validated address is unreachable: the next one is tried within the connect budget (RF-10).
- Update that sends `url` equal to the stored unsafe URL together with a `name` change: rejected (RF-1). An update that leaves out `url` on a service with an unsafe URL is allowed. Its checks stay blocked.
- Reactivating a deactivated service with an unsafe URL: allowed. Its checks are blocked.
- Precedence: reserved name or unsafe literal on an unknown service id gives 422 (RF-2). A hostname that resolves to a private address on an unknown service id gives 404, because resolution happens only after the service is found (RF-3). An unsafe URL that duplicates an existing service on create gives 422 (RF-3).
- A server answering with a redirect to `http://127.0.0.1/`: not followed (RF-13).
- `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` set in the environment: ignored by checks (RF-14). `SSL_CERT_FILE` / `SSL_CERT_DIR` are still honored.
- Cloud or platform endpoints on public addresses (for example `168.63.129.16`): not blocked, because only non-public addresses are blocked.
- IPv6 literal with a zone ID (`[fe80::1%25eth0]`): already rejected as an invalid URL. No new rule.
- Blocked and unresolvable checks count as failures for UP/DOWN and 24h uptime under the existing rules. They are real outcomes (the check was refused or could not run), not fabricated data. The status endpoint shows only DOWN. The reason is the error message in the service's metrics.

## Out of scope
- An allow-list or setting to permit private destinations (for example to monitor an internal network). The owner chose to block all non-public destinations.
- Blocking other special names such as `.internal` or `.local` by name. They are judged by resolution.
- Blocking provider-specific endpoints on public addresses (for example `168.63.129.16`).
- Sending checks through a proxy, and `.netrc` credentials for checks.
- Following redirects safely (validating each hop).
- Restricting ports, schemes beyond `http`/`https`, or URL paths.
- Auto-deactivating or deleting services with unsafe URLs, and any change to stored checks.
- Outbound network controls outside the application (firewall, egress proxy, container network policy).
- Throughput or concurrency limits beyond RNF-2 and RNF-4.
- Known gaps, pre-existing, for a future spec: `url: null` in an update fails; an update to a URL that duplicates another service returns 500 instead of 409.
- Authentication of write endpoints (roadmap api-auth spec).
- Rate limiting or abuse controls for public destinations.

## Completion criteria
- Every "rejected" case of RNF-6 returns 422 with the RF-5 entry, and no service is created, changed or rescheduled. Every "accepted" case succeeds. Every "precedence" case returns the stated status.
- A check for a service whose host is a reserved name, a non-public literal or resolves to a non-public address sends no request and stores a DOWN check with the RF-7 or RF-8 message. The status endpoint shows it DOWN once the failure threshold is reached.
- A check whose host does not resolve stores a DOWN check starting with `resolution failed:`.
- A check of a public HTTPS URL connects only to a validated address, verifies the certificate against the hostname and gives the same result as before (RNF-1).
- The RNF-2 time bound and the RNF-4 responsiveness are shown by automated tests.
- No 422 response and no stored error message contains a resolved IP address.
- A service already stored with an unsafe URL keeps its row and history, and its next check is blocked.
- Checks never follow redirects, ignore environment proxies and still honor custom CA settings.
- All quality checks in RNF-10 are green, and the default test run passes under the RNF-7 guard.
- The docs listed in RF-17 are updated, including the roadmap numbering.
- A manual compose run confirms that `POST /api/v1/services` with `http://169.254.169.254/` and with `http://db:5432/` returns 422, a public URL is accepted and checked, and `GET /health` and `GET /api/v1/status` still respond.

## Open questions
- None. Owner decisions (2026-11-12): block all non-public destinations with no allow-list (Q1); validate at create/update and again at every check (Q2); keep existing unsafe services and record blocked checks as failures (Q3); accept unresolvable hostnames at registration (Q4); number this spec 002 and shift the roadmap (Q5). Clarify pass 1 (33 items) and pass 2 (19 items) resolved with the owner's approval of the proposed answers. Pass 2 reversed two pass-1 answers: NAT64 `64:ff9b::/96` is judged by its embedded IPv4 instead of blocked outright, and the single 422 precedence rule is split (no-DNS checks before 404, DNS checks after 404 and before 409).
