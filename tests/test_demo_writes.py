"""Demo-mode write rules (spec 003a RF-10 to RF-16). Fixture caps: global 4, per host 2, min 300 s."""

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.demo_settings import DemoConfig
from app.db.models.services import Service
from tests.conftest import FakeResolver

BASE = "/api/v1/services"
SEEDED_MSG = {"detail": "Seeded demo services cannot be changed."}
ORDINARY_MSG = {"detail": "This service cannot be changed while demo mode is on."}
GLOBAL_MSG = {"detail": "Demo limit reached: at most 4 visitor services at a time."}
DUPLICATE_MSG = {"detail": "A service with this URL already exists"}


def _host_msg(host: str) -> dict[str, str]:
    return {"detail": f"Demo limit reached: at most 2 visitor services for host {host}."}


def _interval_error(value: int) -> dict[str, Any]:
    return {
        "detail": [
            {
                "type": "demo_interval_too_low",
                "loc": ["body", "check_interval"],
                "msg": "In demo mode the check interval must be at least 300 seconds",
                "input": value,
            }
        ]
    }


def _store(db: Session, url: str, origin: str = "visitor", interval: int = 300) -> Service:
    svc = Service(
        name="Stored",
        url=url,
        expected_status=200,
        check_interval=interval,
        is_active=True,
        origin=origin,
    )
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return svc


def _body(url: str, **extra: Any) -> dict[str, Any]:
    return {"name": "New", "url": url, **extra}


@pytest.fixture
def schedule_spy() -> Any:
    with (
        patch("app.api.v1.services.schedule_service") as schedule,
        patch("app.api.v1.services.unschedule_service") as unschedule,
    ):
        yield schedule, unschedule


# ---------------------------------------------------------------------------
# RF-12 and RF-13: origin and interval
# ---------------------------------------------------------------------------


class TestVisitorCreate:
    def test_create_is_a_visitor_service_with_the_demo_default_interval(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        resp = client.post(BASE, json=_body("https://new.example/"))
        assert resp.status_code == 201
        assert resp.json()["check_interval"] == 300
        assert db_session.get(Service, resp.json()["id"]).origin == "visitor"  # type: ignore[union-attr]

    def test_interval_above_the_minimum_is_kept(
        self, client: TestClient, demo_config: DemoConfig
    ) -> None:
        resp = client.post(BASE, json=_body("https://new.example/", check_interval=600))
        assert resp.status_code == 201 and resp.json()["check_interval"] == 600

    def test_interval_below_the_minimum_on_create(
        self, client: TestClient, demo_config: DemoConfig
    ) -> None:
        resp = client.post(BASE, json=_body("https://new.example/", check_interval=120))
        assert resp.status_code == 422
        assert resp.json() == _interval_error(120)

    def test_interval_below_the_api_range_fails_validation_first(
        self, client: TestClient, demo_config: DemoConfig
    ) -> None:
        resp = client.post(BASE, json=_body("https://new.example/", check_interval=5))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["type"] == "greater_than_equal"


class TestVisitorUpdateInterval:
    def test_low_interval_on_update(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig, schedule_spy: Any
    ) -> None:
        svc = _store(db_session, "https://v.example/")
        resp = client.patch(f"{BASE}/{svc.id}", json={"check_interval": 60})
        assert resp.status_code == 422 and resp.json() == _interval_error(60)
        schedule_spy[0].assert_not_called()

    def test_low_interval_equal_to_current_is_still_rejected(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        svc = _store(db_session, "https://v.example/", interval=60)
        resp = client.patch(f"{BASE}/{svc.id}", json={"check_interval": 60})
        assert resp.status_code == 422

    def test_absent_interval_is_not_checked(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        svc = _store(db_session, "https://v.example/", interval=60)
        assert client.patch(f"{BASE}/{svc.id}", json={"name": "Renamed"}).status_code == 200


# ---------------------------------------------------------------------------
# RF-10 and RF-11: protection
# ---------------------------------------------------------------------------


class TestProtection:
    @pytest.mark.parametrize(
        ("origin", "expected"), [("seeded", SEEDED_MSG), ("ordinary", ORDINARY_MSG)]
    )
    @pytest.mark.parametrize("body", [{}, {"name": "X"}, {"is_active": False}])
    def test_update_is_forbidden(
        self,
        client: TestClient,
        db_session: Session,
        demo_config: DemoConfig,
        schedule_spy: Any,
        origin: str,
        expected: dict[str, str],
        body: dict[str, Any],
    ) -> None:
        svc = _store(db_session, "https://p.example/", origin=origin)
        resp = client.patch(f"{BASE}/{svc.id}", json=body)
        assert resp.status_code == 403 and resp.json() == expected
        db_session.refresh(svc)
        assert svc.name == "Stored" and svc.is_active is True
        schedule_spy[0].assert_not_called()
        schedule_spy[1].assert_not_called()

    @pytest.mark.parametrize(
        ("origin", "expected"), [("seeded", SEEDED_MSG), ("ordinary", ORDINARY_MSG)]
    )
    def test_delete_is_forbidden(
        self,
        client: TestClient,
        db_session: Session,
        demo_config: DemoConfig,
        schedule_spy: Any,
        origin: str,
        expected: dict[str, str],
    ) -> None:
        svc = _store(db_session, "https://p.example/", origin=origin)
        resp = client.delete(f"{BASE}/{svc.id}")
        assert resp.status_code == 403 and resp.json() == expected
        assert db_session.get(Service, svc.id) is not None
        schedule_spy[1].assert_not_called()

    def test_visitor_services_can_be_changed_and_deleted(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        svc = _store(db_session, "https://v.example/")
        assert client.patch(f"{BASE}/{svc.id}", json={"name": "Mine"}).status_code == 200
        assert client.delete(f"{BASE}/{svc.id}").status_code == 204


# ---------------------------------------------------------------------------
# RF-15: caps
# ---------------------------------------------------------------------------


class TestCaps:
    def test_global_cap_boundary(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        for i in range(3):
            _store(db_session, f"https://g{i}.example/")
        assert client.post(BASE, json=_body("https://g3.example/")).status_code == 201
        resp = client.post(BASE, json=_body("https://g4.example/"))
        assert resp.status_code == 409 and resp.json() == GLOBAL_MSG

    def test_lowered_global_cap(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        for i in range(6):
            _store(db_session, f"https://g{i}.example/")
        assert client.post(BASE, json=_body("https://g9.example/")).json() == GLOBAL_MSG

    def test_ordinary_and_seeded_do_not_count(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        for i in range(4):
            _store(db_session, f"https://o{i}.example/", origin="ordinary" if i % 2 else "seeded")
        assert client.post(BASE, json=_body("https://new.example/")).status_code == 201

    def test_per_host_cap_uses_the_cap_host(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        _store(db_session, "https://h.example/a")
        _store(db_session, "https://H.example./b")  # same cap host: case and trailing dot
        resp = client.post(BASE, json=_body("https://h.example:8443/c"))
        assert resp.status_code == 409 and resp.json() == _host_msg("h.example")

    def test_update_into_a_full_host_is_rejected(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        _store(db_session, "https://h.example/a")
        _store(db_session, "https://h.example/b")
        other = _store(db_session, "https://other.example/")
        resp = client.patch(f"{BASE}/{other.id}", json={"url": "https://h.example/c"})
        assert resp.status_code == 409 and resp.json() == _host_msg("h.example")
        db_session.refresh(other)
        assert other.url == "https://other.example/"

    def test_update_within_the_same_host_is_allowed_when_full(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        first = _store(db_session, "https://h.example/a")
        _store(db_session, "https://h.example/b")
        resp = client.patch(f"{BASE}/{first.id}", json={"url": "https://h.example/a2"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# RF-16: order of rejections (one test per adjacent pair)
# ---------------------------------------------------------------------------


class TestCreateOrder:
    def test_validation_before_demo_interval(
        self, client: TestClient, demo_config: DemoConfig
    ) -> None:
        resp = client.post(
            BASE, json={"url": "https://x.example/", "check_interval": 120}
        )  # no name
        assert resp.status_code == 422 and resp.json()["detail"][0]["type"] == "missing"

    def test_demo_interval_before_global_cap(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        for i in range(4):
            _store(db_session, f"https://g{i}.example/")
        resp = client.post(BASE, json=_body("https://x.example/", check_interval=120))
        assert resp.json() == _interval_error(120)

    def test_global_cap_before_per_host_cap(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        _store(db_session, "https://h.example/a")
        _store(db_session, "https://h.example/b")
        _store(db_session, "https://g1.example/")
        _store(db_session, "https://g2.example/")
        assert client.post(BASE, json=_body("https://h.example/c")).json() == GLOBAL_MSG

    def test_per_host_cap_before_hostname_check(
        self,
        client: TestClient,
        db_session: Session,
        demo_config: DemoConfig,
        fake_resolver: FakeResolver,
    ) -> None:
        _store(db_session, "https://h.example/a")
        _store(db_session, "https://h.example/b")
        fake_resolver.set("h.example", "10.0.0.5")
        resp = client.post(BASE, json=_body("https://h.example/c"))
        assert resp.json() == _host_msg("h.example")
        assert fake_resolver.calls == []  # a refused request causes no DNS lookup

    def test_hostname_check_before_duplicate(
        self,
        client: TestClient,
        db_session: Session,
        demo_config: DemoConfig,
        fake_resolver: FakeResolver,
    ) -> None:
        _store(db_session, "https://private.example/")
        fake_resolver.set("private.example", "10.0.0.5")
        resp = client.post(BASE, json=_body("https://private.example/"))
        assert resp.status_code == 422 and resp.json()["detail"][0]["type"] == "url_not_allowed"

    def test_duplicate_last(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        _store(db_session, "https://dup.example/")
        assert client.post(BASE, json=_body("https://dup.example/")).json() == DUPLICATE_MSG


class TestUpdateOrder:
    def test_validation_before_404(self, client: TestClient, demo_config: DemoConfig) -> None:
        resp = client.patch(f"{BASE}/9999", json={"name": None})
        assert resp.status_code == 422

    def test_404_before_403(self, client: TestClient, demo_config: DemoConfig) -> None:
        assert client.patch(f"{BASE}/9999", json={}).status_code == 404

    def test_403_before_demo_interval(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        svc = _store(db_session, "https://s.example/", origin="seeded")
        resp = client.patch(f"{BASE}/{svc.id}", json={"check_interval": 60})
        assert resp.json() == SEEDED_MSG

    def test_demo_interval_before_per_host_cap(
        self, client: TestClient, db_session: Session, demo_config: DemoConfig
    ) -> None:
        _store(db_session, "https://h.example/a")
        _store(db_session, "https://h.example/b")
        other = _store(db_session, "https://other.example/")
        resp = client.patch(
            f"{BASE}/{other.id}", json={"url": "https://h.example/c", "check_interval": 60}
        )
        assert resp.json() == _interval_error(60)

    def test_per_host_cap_before_hostname_check(
        self,
        client: TestClient,
        db_session: Session,
        demo_config: DemoConfig,
        fake_resolver: FakeResolver,
    ) -> None:
        _store(db_session, "https://h.example/a")
        _store(db_session, "https://h.example/b")
        other = _store(db_session, "https://other.example/")
        fake_resolver.set("h.example", "10.0.0.5")
        resp = client.patch(f"{BASE}/{other.id}", json={"url": "https://h.example/c"})
        assert resp.json() == _host_msg("h.example") and fake_resolver.calls == []

    def test_hostname_check_before_duplicate(
        self,
        client: TestClient,
        db_session: Session,
        demo_config: DemoConfig,
        fake_resolver: FakeResolver,
    ) -> None:
        _store(db_session, "https://private.example/")
        other = _store(db_session, "https://other.example/")
        fake_resolver.set("private.example", "10.0.0.5")
        resp = client.patch(f"{BASE}/{other.id}", json={"url": "https://private.example/"})
        assert resp.json()["detail"][0]["type"] == "url_not_allowed"


class TestDeleteOrder:
    def test_validation_before_404(self, client: TestClient, demo_config: DemoConfig) -> None:
        assert client.delete(f"{BASE}/abc").status_code == 422

    def test_404_before_403(self, client: TestClient, demo_config: DemoConfig) -> None:
        assert client.delete(f"{BASE}/9999").status_code == 404
