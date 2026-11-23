# Validation - 002 URL safety

Branch `feature/002-url-safety`, local evidence collected 2026-11-12 on WSL2 (Python 3.11, Docker 29.2.1 / Compose v2.40.3). `/sdd-validate` re-runs everything rather than trusting this file.

Commits: `de71108` (spec/plan/tasks), T1 `b6d797b`, T2 `08106fc`, T3 `3424ccc`, T4 `0f0558c`, T5 `ee75291`, T6 `228a5ef`, T7 `ebe869c`, T8 `3f835dd`, T9 `745da12`.

## Completion criteria

| Criterion | Result | Evidence |
|---|---|---|
| Every RNF-6 "rejected" case returns 422 with the RF-5 entry; no create/change/reschedule. Every "accepted" case succeeds. Every "precedence" case returns the stated status | PASS | `tests/test_services_url_safety.py`: 22 URLs x POST and PATCH assert the exact body, row count/unchanged URL and `schedule_service` not called; hostname cases (private, mixed v4, mixed v6); accepted public literals, public hostname, unresolvable (3 reasons); precedence: reserved + unknown id -> 422, resolves-private + unknown id -> 404, resolves-private duplicate -> 422, empty name + reserved -> one 422 with 2 entries, PATCH re-sending stored unsafe URL -> 422, PATCH without url -> 200 |
| A check for a reserved name / non-public literal / host resolving to non-public sends no request and stores DOWN with the RF-7/RF-8 message; status shows DOWN after the threshold | PASS | `tests/test_monitor.py::TestCheckTimeDestination` (`httpx.Client` never called). Compose: legacy service `http://localhost:8000/health` stored 4 metrics, all `unsafe destination: host is not a public address`; `/api/v1/status` showed it `UP` after 1 failure and `DOWN` after 3 (`FAIL_THRESHOLD=3`) |
| A check whose host does not resolve stores DOWN starting with `resolution failed:` | PASS | `test_unresolvable_hostname_fails_without_request` (3 reasons, exact text `resolution failed: gone.test: <reason>`) |
| A public HTTPS check connects only to a validated address, verifies the certificate against the hostname, same result as before | PASS | Unit: `test_https_hostname_is_pinned_with_host_header_and_sni`, `test_client_ignores_environment_and_redirects` (`check_hostname=True`, `CERT_REQUIRED`). Real TLS: `test_tls_keeps_sni_host_and_verification_with_custom_ca` (server saw SNI and Host `monitor.test`), `test_tls_hostname_mismatch_fails_without_leaking_the_address`. Compose: `https://example.com/` -> UP, status code 200, response times 444.27 / 104.48 ms |
| RNF-2 time bound and RNF-4 responsiveness shown by automated tests | PASS | `test_connect_budget_is_shared_by_all_attempts` (3 blackholed addresses, sum of connect timeouts <= 1.05 s with `CHECK_TIMEOUT_SECONDS=1`); `test_slow_resolution_does_not_block_other_requests` (`/health` and `/api/v1/status` each < 500 ms during a 1.0 s resolution, worker still alive) |
| No 422 response and no stored error contains a resolved IP | PASS | `test_create_with_hostname_resolving_to_non_public_is_rejected` (addresses not in body), `test_error_messages_never_contain_resolved_addresses`, `test_tls_hostname_mismatch_fails_without_leaking_the_address`, `test_redact_*` (6 cases) |
| A stored unsafe service keeps its row and history; its next check is blocked | PASS | `test_stored_unsafe_service_is_kept_and_its_check_is_blocked`; compose legacy row inserted with SQL, still active and listed after its blocked checks |
| Checks never follow redirects, ignore environment proxies, honor custom CA settings | PASS | Real loopback servers: `test_redirect_to_private_address_is_not_followed` (status 302 stored, exactly one request), `test_environment_proxies_are_ignored` (5 proxy variables set to `198.51.100.1:3128`), `test_tls_keeps_sni_host_and_verification_with_custom_ca` (trust only via `SSL_CERT_FILE`) |
| RNF-10 checks green; default run passes under the RNF-7 guard | PASS (local) | `ruff check .` -> All checks passed; `ruff format --check .` -> 71 files already formatted; `mypy` -> no issues in 41 source files; `pytest` -> 322 passed, coverage 94.86% (floor 88, baseline 88.41%) |
| Docs listed in RF-17 updated, including roadmap numbering | PASS | `docs/architecture.md` §3 (sequence), §5 (settings), §6 (URL safety), §7; `docs/SPEC.md` scope + roadmap 002-006; `.env.example`; OpenAPI diff vs `master`: only the two `url` descriptions added (`diff` output 2 lines). PENDING: Mermaid render check in the GitHub preview (T9) Mermaid diagrams confirmed rendering in the GitHub PR #5 file view by the owner (2026-11-23). |
| Manual compose: 169.254.169.254 and db:5432 -> 422, public URL accepted and checked, /health and /status respond | PASS | `docker compose up -d --build` (temporary `.env` copied from `.env.example`, removed after): `POST http://169.254.169.254/latest/meta-data/` -> 422, `POST http://db:5432/` -> 422 (`db` resolves to 172.18.0.2 in the container), `POST http://localhost:8000/health` -> 422, `POST https://example.com/` -> 201 then UP metrics; `GET /health` -> `{"status":"ok"}`; `GET /api/v1/status` -> 200. Test services deleted afterwards (DB back to 0 services) |

## Non-functional requirements

| RNF | Result | Evidence |
|---|---|---|
| RNF-1 same outcome for public URLs | PASS | Existing `check_service` tests pass with only import lines changed (`git diff master -- tests/test_monitor.py`: 3 removed lines, all imports); `test_response_time_includes_resolution`; TCP-only fallback (`test_tls_failure_does_not_try_another_address`); compose `example.com` UP 200 |
| RNF-2 bounded check | PASS | See budget test above; worst case = resolution timeout + connect budget + per-read/write timeout |
| RNF-3 invalid timeout stops startup | PASS | `test_invalid_resolution_timeout_is_rejected` (`"abc"`, 0, -1, 30.5 -> `ValidationError` from `Settings`, which is built at import); valid 0.1 / 30 / "1.5"; default 2.0 |
| RNF-4 API responsive during slow resolution | PASS | `test_slow_resolution_does_not_block_other_requests` |
| RNF-5 scheduler never crashes | PASS | `check_service` catches every exception; `check_service_by_id` unchanged; compose logs show checks every 10 s with no traceback |
| RNF-6 test coverage list | PASS | Rejected / accepted / precedence / check-time / configuration lists in `tests/test_services_url_safety.py`, `tests/test_monitor.py`, `tests/test_url_safety.py` (table tests cover every row) |
| RNF-7 offline default run | PASS | Guard tests `tests/test_network_guard.py` (4); `unshare -rn pytest` (no network namespace) -> 322 passed |
| RNF-8 existing tests keep their assertions | PASS | Only imports changed in `tests/test_monitor.py`; `tests/test_services.py` untouched; new behavior in new test files/classes |
| RNF-9 no schema/response change except the new 422 | PASS | No migration; `python -m tools.schema_check` -> "No schema differences between the migration history and the models."; OpenAPI diff = 2 descriptions |
| RNF-10 spec 000/001 checks green | PASS | Local: lint, format, mypy, pytest (94.86%), schema check, `pytest -m postgres --no-cov` -> 12 passed. CI: PR #5 run 45271865825 on `4ccd38a`: `lint`, `format`, `typecheck`, `test`, `migrations` all pass, 46 s (2026-11-23 21:39:19 to 21:40:05 UTC). Re-checked locally before push: 322 passed, 12 deselected. |
| RNF-11 no new dependency | PASS | `requirements*.txt` unchanged (`git diff master --stat -- requirements.txt requirements-dev.txt` empty) |

## Notes
- The test TLS key pair under `tests/fixtures/tls/` is owner-approved (plan D10) and used only by loopback tests.
- During planning, one probe line resolved `example.com` through real DNS; no other probe used the network.
- T9 ticked 2026-11-23 (Mermaid render confirmed, PR #5). T10 stays unticked until `/sdd-validate 002-url-safety` passes.
