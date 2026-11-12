"""URL safety on POST/PATCH /api/v1/services (spec 002 RF-1 to RF-5, RNF-4)."""

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.services import Service
from app.services.url_safety import NOT_ALLOWED_MESSAGE
from tests.conftest import FakeResolver

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

    def test_update_with_null_url_is_not_classified(self, client: TestClient) -> None:
        # `url: null` is a pre-existing gap (spec 002 Out of scope); the validator skips None.
        resp = client.patch(f"{BASE}/9999", json={"name": "x", "url": None})
        assert resp.status_code == 404
