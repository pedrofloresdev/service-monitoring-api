"""
Endpoint tests for /api/v1/services.
Covers: create, list, get, patch, delete — including error cases.
"""
import pytest

BASE = "/api/v1/services"
VALID_PAYLOAD = {
    "name": "My API",
    "url": "https://api.example.com/health",
    "expected_status": 200,
    "check_interval": 60,
}


# ---------------------------------------------------------------------------
# POST /services
# ---------------------------------------------------------------------------

class TestCreateService:
    def test_returns_201_and_service_data(self, client):
        resp = client.post(BASE, json=VALID_PAYLOAD)
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "My API"
        assert body["expected_status"] == 200
        assert body["check_interval"] == 60
        assert body["is_active"] is True
        assert "id" in body
        assert "created_at" in body

    def test_duplicate_url_returns_409(self, client):
        client.post(BASE, json=VALID_PAYLOAD)
        resp = client.post(BASE, json=VALID_PAYLOAD)
        assert resp.status_code == 409

    def test_invalid_url_returns_422(self, client):
        resp = client.post(BASE, json={**VALID_PAYLOAD, "url": "not-a-url"})
        assert resp.status_code == 422

    def test_interval_below_minimum_returns_422(self, client):
        resp = client.post(BASE, json={**VALID_PAYLOAD, "check_interval": 5})
        assert resp.status_code == 422

    def test_interval_above_maximum_returns_422(self, client):
        resp = client.post(BASE, json={**VALID_PAYLOAD, "check_interval": 99999})
        assert resp.status_code == 422

    def test_invalid_expected_status_returns_422(self, client):
        resp = client.post(BASE, json={**VALID_PAYLOAD, "expected_status": 99})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /services
# ---------------------------------------------------------------------------

class TestListServices:
    def test_returns_empty_list_when_no_services(self, client):
        resp = client.get(BASE)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_services(self, client):
        client.post(BASE, json=VALID_PAYLOAD)
        client.post(BASE, json={**VALID_PAYLOAD, "url": "https://other.example.com/health"})
        resp = client.get(BASE)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_active_only_filter_excludes_inactive(self, client, service, db_session):
        from app.db.models.services import Service as ServiceModel
        db_session.query(ServiceModel).filter(ServiceModel.id == service.id).update({"is_active": False})
        db_session.commit()

        resp = client.get(BASE, params={"active_only": True})
        assert resp.status_code == 200
        assert all(s["is_active"] for s in resp.json())

    def test_without_active_only_returns_inactive_too(self, client, service, db_session):
        from app.db.models.services import Service as ServiceModel
        db_session.query(ServiceModel).filter(ServiceModel.id == service.id).update({"is_active": False})
        db_session.commit()

        resp = client.get(BASE)
        assert resp.status_code == 200
        assert any(not s["is_active"] for s in resp.json())


# ---------------------------------------------------------------------------
# GET /services/{id}
# ---------------------------------------------------------------------------

class TestGetService:
    def test_returns_service(self, client, service):
        resp = client.get(f"{BASE}/{service.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == service.id
        assert resp.json()["name"] == service.name

    def test_unknown_id_returns_404(self, client):
        resp = client.get(f"{BASE}/9999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /services/{id}
# ---------------------------------------------------------------------------

class TestUpdateService:
    def test_update_name(self, client, service):
        resp = client.patch(f"{BASE}/{service.id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_check_interval(self, client, service):
        resp = client.patch(f"{BASE}/{service.id}", json={"check_interval": 120})
        assert resp.status_code == 200
        assert resp.json()["check_interval"] == 120

    def test_deactivate_service(self, client, service):
        resp = client.patch(f"{BASE}/{service.id}", json={"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_partial_update_preserves_other_fields(self, client, service):
        resp = client.patch(f"{BASE}/{service.id}", json={"name": "Only Name Changed"})
        body = resp.json()
        assert body["url"] == service.url
        assert body["check_interval"] == service.check_interval

    def test_unknown_id_returns_404(self, client):
        resp = client.patch(f"{BASE}/9999", json={"name": "Ghost"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /services/{id}
# ---------------------------------------------------------------------------

class TestDeleteService:
    def test_delete_returns_204(self, client, service):
        resp = client.delete(f"{BASE}/{service.id}")
        assert resp.status_code == 204

    def test_deleted_service_no_longer_found(self, client, service):
        client.delete(f"{BASE}/{service.id}")
        resp = client.get(f"{BASE}/{service.id}")
        assert resp.status_code == 404

    def test_unknown_id_returns_404(self, client):
        resp = client.delete(f"{BASE}/9999")
        assert resp.status_code == 404
