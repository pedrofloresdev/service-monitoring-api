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
