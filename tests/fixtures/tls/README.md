# Test TLS certificate (spec 002)

`monitor-test.crt` / `monitor-test.key` are a self-signed certificate and key for the
name `monitor.test`, used **only** by the loopback TLS tests in `tests/test_monitor.py`
(SNI and hostname verification, `SSL_CERT_FILE` trust). They protect nothing and are not
secrets. Owner-approved in `specs/002-url-safety/plan.md` (decision D10).

Regenerate (valid ~100 years):

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 36500 \
  -subj "/CN=monitor.test" -addext "subjectAltName=DNS:monitor.test" \
  -keyout monitor-test.key -out monitor-test.crt
```
