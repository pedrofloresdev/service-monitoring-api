"""Service write rules that apply with demo mode on and off (spec 003a RF-14, RF-16, RF-18, RF-19)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.services import Service

BASE = "/api/v1/services"
NULLABLE_FIELDS = ["name", "url", "expected_status", "check_interval", "is_active"]


def _store(db: Session, url: str = "https://stored.example/", **extra: Any) -> Service:
    svc = Service(
        name="Stored", url=url, expected_status=200, check_interval=60, is_active=True, **extra
    )
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return svc


# ---------------------------------------------------------------------------
# RF-19: null on required fields
# ---------------------------------------------------------------------------


class TestNullFields:
    @pytest.mark.parametrize("field", NULLABLE_FIELDS)
    def test_null_field_is_a_validation_error(
        self, client: TestClient, db_session: Session, field: str
    ) -> None:
        svc = _store(db_session)
        resp = client.patch(f"{BASE}/{svc.id}", json={field: None})
        assert resp.status_code == 422
        assert resp.json()["detail"] == [
            {
                "type": "null_not_allowed",
                "loc": ["body", field],
                "msg": f"{field} cannot be null",
                "input": None,
            }
        ]
        db_session.refresh(svc)
        assert svc.name == "Stored" and svc.is_active is True

    def test_one_entry_per_null_field(self, client: TestClient, db_session: Session) -> None:
        svc = _store(db_session)
        resp = client.patch(f"{BASE}/{svc.id}", json={"url": None, "is_active": None, "name": "ok"})
        assert resp.status_code == 422
        assert [e["loc"][-1] for e in resp.json()["detail"]] == ["url", "is_active"]

    def test_null_is_rejected_in_demo_mode_too(
        self, client: TestClient, db_session: Session, demo_config: Any
    ) -> None:
        svc = _store(db_session, origin="visitor")
        resp = client.patch(f"{BASE}/{svc.id}", json={"check_interval": None})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["type"] == "null_not_allowed"


# ---------------------------------------------------------------------------
# RF-14: duplicate URLs never give 500 (demo mode off here)
# ---------------------------------------------------------------------------

PAYLOAD = {
    "name": "New",
    "url": "https://dup.example/",
    "expected_status": 200,
    "check_interval": 60,
}


class TestDuplicates:
    def test_exact_duplicate_on_create(self, client: TestClient, db_session: Session) -> None:
        _store(db_session, "https://dup.example/")
        resp = client.post(BASE, json=PAYLOAD)
        assert resp.status_code == 409
        assert resp.json() == {"detail": "A service with this URL already exists"}

    def test_legacy_form_duplicate_on_create(self, client: TestClient, db_session: Session) -> None:
        _store(db_session, "https://DUP.example")  # written outside the API, non-canonical
        resp = client.post(BASE, json=PAYLOAD)
        assert resp.status_code == 409

    def test_duplicate_on_update_is_409_not_500(
        self, client: TestClient, db_session: Session
    ) -> None:
        _store(db_session, "https://dup.example/")
        other = _store(db_session, "https://other.example/")
        resp = client.patch(f"{BASE}/{other.id}", json={"url": "https://dup.example/"})
        assert resp.status_code == 409
        db_session.refresh(other)
        assert other.url == "https://other.example/"

    def test_legacy_form_duplicate_on_update(self, client: TestClient, db_session: Session) -> None:
        _store(db_session, "https://Dup.Example")
        other = _store(db_session, "https://other.example/")
        resp = client.patch(f"{BASE}/{other.id}", json={"url": "https://dup.example/"})
        assert resp.status_code == 409

    def test_keeping_own_url_is_not_a_duplicate(
        self, client: TestClient, db_session: Session
    ) -> None:
        svc = _store(db_session, "https://own.example/")
        resp = client.patch(
            f"{BASE}/{svc.id}", json={"url": "https://own.example/", "name": "Renamed"}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_unique_race_is_reported_as_duplicate(
        self, client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Layer 1 misses (another request inserts between the check and the commit): the
        # database's constraint fires and layer 2 turns it into a 409 (RF-14).
        from app.services import service_writes

        real_check = service_writes.duplicate_exists
        calls = {"n": 0}

        def miss_first(db: Session, url: str, exclude_id: int | None) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                _store(db_session, url)  # the competing insert
                return False
            return real_check(db, url, exclude_id)

        monkeypatch.setattr(service_writes, "duplicate_exists", miss_first)
        resp = client.post(BASE, json=PAYLOAD)
        assert resp.status_code == 409

    def test_other_database_errors_are_not_duplicates(
        self, client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        def broken_commit() -> None:
            raise IntegrityError("INSERT ...", {}, Exception("NOT NULL constraint failed"))

        monkeypatch.setattr(db_session, "commit", broken_commit)
        with pytest.raises(IntegrityError):
            client.post(BASE, json=PAYLOAD)


# ---------------------------------------------------------------------------
# RF-16: a rejected write changes nothing, and schedules change only after a commit
# ---------------------------------------------------------------------------


class TestCommitOrder:
    def test_rejected_update_does_not_touch_the_schedule(
        self, client: TestClient, db_session: Session
    ) -> None:
        from unittest.mock import patch

        _store(db_session, "https://dup.example/")
        other = _store(db_session, "https://other.example/")
        with (
            patch("app.api.v1.services.schedule_service") as schedule,
            patch("app.api.v1.services.unschedule_service") as unschedule,
        ):
            client.patch(f"{BASE}/{other.id}", json={"url": "https://dup.example/", "name": "X"})
        schedule.assert_not_called()
        unschedule.assert_not_called()
        db_session.refresh(other)
        assert other.name == "Stored"

    def test_delete_unschedules_after_commit(self, client: TestClient, db_session: Session) -> None:
        from unittest.mock import patch

        svc = _store(db_session)
        order: list[str] = []
        real_commit = db_session.commit

        def tracking_commit() -> None:
            order.append("commit")
            real_commit()

        with (
            patch.object(db_session, "commit", tracking_commit),
            patch(
                "app.api.v1.services.unschedule_service",
                side_effect=lambda _id: order.append("unschedule"),
            ),
        ):
            assert client.delete(f"{BASE}/{svc.id}").status_code == 204
        assert order == ["commit", "unschedule"]

    def test_demo_off_has_no_caps_or_protection(
        self, client: TestClient, db_session: Session
    ) -> None:
        svc = _store(db_session, origin="seeded")
        assert client.patch(f"{BASE}/{svc.id}", json={"name": "Free"}).status_code == 200
        for i in range(30):
            body = {**PAYLOAD, "url": f"https://h{i}.example/"}
            assert client.post(BASE, json=body).status_code == 201
