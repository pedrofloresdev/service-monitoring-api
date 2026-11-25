"""URL safety on POST/PATCH /api/v1/services (spec 002 RF-1 to RF-5, RNF-4)."""

import socket
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.services import Service
from app.services.url_safety import NOT_ALLOWED_MESSAGE
from tests.conftest import FakeResolver, PartialDns

BASE = "/api/v1/services"


def _payload(url: str, **extra: Any) -> dict[str, Any]:
    return {"name": "Svc", "url": url, **extra}


def _url_error(submitted: str) -> dict[str, Any]:
    return {
        "type": "url_not_allowed",
        "loc": ["body", "url"],
        "msg": NOT_ALLOWED_MESSAGE,
        "input": submitted,
    }


# Rejected during request validation, without DNS (RF-2).
REJECTED_WITHOUT_DNS = [
    "http://localhost/",
    "http://LOCALHOST./",
    "http://localhost../",
    "http://app.localhost:8000/x",
    "http://127.0.0.1/",
    "http://[::1]:8080/",
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://0.0.0.0/",
    "http://100.64.0.1/",
    "http://240.0.0.1/",
    "http://[::ffff:127.0.0.1]/",
    "http://[::7f00:1]/",
    "http://[64:ff9b::a00:1]/",
    "http://[64:ff9b:1::1]/",
    "http://[2002:a00:1::]/",
    "http://[2001::1]/",
    "http://2130706433/",
    "http://127.1/",
    "http://user@10.0.0.1:8443/",
]

ACCEPTED_LITERALS = [
    "http://8.8.8.8/",
    "http://[::ffff:8.8.8.8]/",
    "http://[::808:808]/",
    "http://[64:ff9b::808:808]/",
]


class TestRejectedDuringValidation:
    @pytest.mark.parametrize("url", REJECTED_WITHOUT_DNS)
    def test_create_is_rejected(
        self, client: TestClient, db_session: Session, fake_resolver: FakeResolver, url: str
    ) -> None:
        with patch("app.api.v1.services.schedule_service") as schedule:
            resp = client.post(BASE, json=_payload(url))

        assert resp.status_code == 422
        assert resp.json() == {"detail": [_url_error(url)]}
        assert db_session.query(Service).count() == 0
        schedule.assert_not_called()
        assert fake_resolver.calls == []

    @pytest.mark.parametrize("url", REJECTED_WITHOUT_DNS)
    def test_update_is_rejected(
        self,
        client: TestClient,
        db_session: Session,
        service: Service,
        fake_resolver: FakeResolver,
        url: str,
    ) -> None:
        with patch("app.api.v1.services.schedule_service") as schedule:
            resp = client.patch(f"{BASE}/{service.id}", json={"url": url})

        assert resp.status_code == 422
        assert resp.json() == {"detail": [_url_error(url)]}
        db_session.refresh(service)
        assert service.url == "https://example.com/health"
        schedule.assert_not_called()
        assert fake_resolver.calls == []

    def test_other_field_errors_are_listed_in_the_same_response(self, client: TestClient) -> None:
        resp = client.post(BASE, json={"name": "", "url": "http://localhost/"})

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert [entry["loc"] for entry in detail] == [["body", "name"], ["body", "url"]]
        assert detail[1] == _url_error("http://localhost/")

    def test_takes_precedence_over_unknown_service(self, client: TestClient) -> None:
        resp = client.patch(f"{BASE}/9999", json={"url": "http://localhost/"})
        assert resp.status_code == 422


class TestAcceptedLiterals:
    @pytest.mark.parametrize("url", ACCEPTED_LITERALS)
    def test_public_literal_is_accepted_without_dns(
        self, client: TestClient, fake_resolver: FakeResolver, url: str
    ) -> None:
        resp = client.post(BASE, json=_payload(url))
        assert resp.status_code == 201
        assert fake_resolver.calls == []

    def test_update_with_null_url_is_a_validation_error(self, client: TestClient) -> None:
        # Spec 002 left `url: null` as a known gap; spec 003a RF-19 closes it. The 422 comes
        # from request validation, so it wins over the 404 for an unknown id (003a RF-16).
        resp = client.patch(f"{BASE}/9999", json={"name": "x", "url": None})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["type"] == "null_not_allowed"


# ---------------------------------------------------------------------------
# Hostname resolution after request validation (RF-3, RF-4, RF-5)
# ---------------------------------------------------------------------------


def _store(db_session: Session, url: str) -> Service:
    svc = Service(name="Stored", url=url, expected_status=200, check_interval=60, is_active=True)
    db_session.add(svc)
    db_session.commit()
    db_session.refresh(svc)
    return svc


class TestHostnameCheck:
    @pytest.mark.parametrize(
        "addresses", [("10.0.0.5",), ("93.184.216.34", "10.0.0.5"), ("2606:4700::1", "fd00::5")]
    )
    def test_create_with_hostname_resolving_to_non_public_is_rejected(
        self,
        client: TestClient,
        db_session: Session,
        fake_resolver: FakeResolver,
        addresses: tuple[str, ...],
    ) -> None:
        fake_resolver.set("internal.test", *addresses)
        submitted = "http://Internal.TEST/status"

        with patch("app.api.v1.services.schedule_service") as schedule:
            resp = client.post(BASE, json=_payload(submitted))

        assert resp.status_code == 422
        assert resp.json() == {"detail": [_url_error(submitted)]}
        assert fake_resolver.calls == ["internal.test"]
        assert db_session.query(Service).count() == 0
        schedule.assert_not_called()
        for address in addresses:
            assert address not in resp.text  # RF-15

    @pytest.mark.parametrize(
        ("family", "address"),
        [(socket.AF_INET, "10.0.0.5"), (socket.AF_INET6, "fd00::5")],
        ids=["private-ipv4-ipv6-fails", "private-ipv6-ipv4-fails"],
    )
    def test_create_with_partial_answer_containing_private_address_is_rejected(
        self,
        client: TestClient,
        db_session: Session,
        partial_dns: PartialDns,
        family: int,
        address: str,
    ) -> None:
        # Real resolver: one family returns a private address, the other lookup fails (RNF-6).
        partial_dns(family, address)
        submitted = "http://partial.test/status"

        with patch("app.api.v1.services.schedule_service") as schedule:
            resp = client.post(BASE, json=_payload(submitted))

        assert resp.status_code == 422
        assert resp.json() == {"detail": [_url_error(submitted)]}
        assert db_session.query(Service).count() == 0
        schedule.assert_not_called()
        assert address not in resp.text  # RF-15

    def test_update_with_partial_answer_containing_private_address_is_rejected(
        self, client: TestClient, db_session: Session, partial_dns: PartialDns
    ) -> None:
        svc = _store(db_session, "https://public.example/")
        partial_dns(socket.AF_INET, "192.168.1.20")
        submitted = "http://partial.test/status"

        with patch("app.api.v1.services.schedule_service") as schedule:
            resp = client.patch(f"{BASE}/{svc.id}", json={"url": submitted})

        assert resp.status_code == 422
        assert resp.json() == {"detail": [_url_error(submitted)]}
        db_session.refresh(svc)
        assert svc.url == "https://public.example/"
        schedule.assert_not_called()
        assert "192.168.1.20" not in resp.text

    @pytest.mark.parametrize("error", ["name not found", "timed out", "lookup failed"])
    def test_unresolvable_hostname_is_accepted(
        self, client: TestClient, fake_resolver: FakeResolver, error: str
    ) -> None:
        fake_resolver.set("not-yet-live.test", error=error)
        resp = client.post(BASE, json=_payload("https://not-yet-live.test/"))
        assert resp.status_code == 201

    def test_public_hostname_is_accepted(
        self, client: TestClient, fake_resolver: FakeResolver
    ) -> None:
        resp = client.post(BASE, json=_payload("https://api.example.com/health"))
        assert resp.status_code == 201
        assert fake_resolver.calls == ["api.example.com"]

    def test_unknown_service_wins_over_hostname_check(
        self, client: TestClient, fake_resolver: FakeResolver
    ) -> None:
        fake_resolver.set("internal.test", "10.0.0.5")
        resp = client.patch(f"{BASE}/9999", json={"url": "http://internal.test/"})
        assert resp.status_code == 404
        assert fake_resolver.calls == []

    def test_hostname_check_wins_over_duplicate(
        self, client: TestClient, db_session: Session, fake_resolver: FakeResolver
    ) -> None:
        _store(db_session, "http://internal.test/")
        fake_resolver.set("internal.test", "10.0.0.5")
        resp = client.post(BASE, json=_payload("http://internal.test/"))
        assert resp.status_code == 422

    def test_update_resending_stored_unsafe_url_is_rejected(
        self, client: TestClient, db_session: Session, fake_resolver: FakeResolver
    ) -> None:
        svc = _store(db_session, "http://internal.test/")
        fake_resolver.set("internal.test", "10.0.0.5")

        with patch("app.api.v1.services.schedule_service") as schedule:
            resp = client.patch(
                f"{BASE}/{svc.id}", json={"name": "Renamed", "url": "http://internal.test/"}
            )

        assert resp.status_code == 422
        db_session.refresh(svc)
        assert svc.name == "Stored"
        schedule.assert_not_called()

    def test_update_without_url_on_unsafe_service_is_allowed(
        self, client: TestClient, db_session: Session, fake_resolver: FakeResolver
    ) -> None:
        svc = _store(db_session, "http://internal.test/")
        fake_resolver.set("internal.test", "10.0.0.5")

        resp = client.patch(f"{BASE}/{svc.id}", json={"name": "Renamed"})

        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"
        assert fake_resolver.calls == []

    def test_update_with_unchanged_url_is_validated_again(
        self, client: TestClient, service: Service, fake_resolver: FakeResolver
    ) -> None:
        resp = client.patch(f"{BASE}/{service.id}", json={"url": service.url})
        assert resp.status_code == 200
        assert fake_resolver.calls == ["example.com"]


# ---------------------------------------------------------------------------
# RNF-4: a slow resolution never stalls the rest of the API
# ---------------------------------------------------------------------------


def test_slow_resolution_does_not_block_other_requests(
    client: TestClient, fake_resolver: FakeResolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RESOLUTION_TIMEOUT_SECONDS", 1.0)
    fake_resolver.delay = 1.0
    started = threading.Event()
    statuses: list[int] = []

    def slow_create() -> None:
        started.set()
        statuses.append(client.post(BASE, json=_payload("https://slow-dns.test/")).status_code)

    worker = threading.Thread(target=slow_create)
    worker.start()
    started.wait()
    time.sleep(0.1)  # let the create reach the resolver

    for path in ("/health", "/api/v1/status"):
        start = time.monotonic()
        assert client.get(path).status_code == 200
        assert time.monotonic() - start < 0.5

    assert worker.is_alive()  # the slow create was still resolving during both requests
    worker.join()
    assert statuses == [201]
