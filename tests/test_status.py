"""
Tests for GET /api/v1/status.
Covers: empty state, UP/DOWN determination via FAIL_THRESHOLD,
uptime calculation, and inactive service exclusion.
"""
from tests.conftest import make_metric
from app.db.models.services import Service

BASE = "/api/v1/status"
FAIL_THRESHOLD = 3  # mirrors settings default


class TestStatusEndpoint:
    def test_empty_returns_zero_counts(self, client):
        resp = client.get(BASE)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_services"] == 0
        assert body["up"] == 0
        assert body["down"] == 0
        assert body["services"] == []

    def test_service_with_no_metrics_is_excluded(self, client, service):
        resp = client.get(BASE)
        assert resp.json()["total_services"] == 0

    def test_service_with_fewer_than_threshold_failures_is_up(self, client, db_session, service):
        for _ in range(FAIL_THRESHOLD - 1):
            make_metric(db_session, service.id, "DOWN")

        resp = client.get(BASE)
        body = resp.json()
        assert body["up"] == 1
        assert body["down"] == 0
        assert body["services"][0]["status"] == "UP"

    def test_service_with_threshold_consecutive_failures_is_down(self, client, db_session, service):
        for _ in range(FAIL_THRESHOLD):
            make_metric(db_session, service.id, "DOWN")

        resp = client.get(BASE)
        body = resp.json()
        assert body["up"] == 0
        assert body["down"] == 1
        assert body["services"][0]["status"] == "DOWN"

    def test_recovery_after_failures_shows_up(self, client, db_session, service):
        for _ in range(FAIL_THRESHOLD):
            make_metric(db_session, service.id, "DOWN")
        make_metric(db_session, service.id, "UP")

        resp = client.get(BASE)
        assert resp.json()["services"][0]["status"] == "UP"

    def test_uptime_percentage_calculation(self, client, db_session, service):
        # 3 UP + 1 DOWN = 75% uptime
        for _ in range(3):
            make_metric(db_session, service.id, "UP")
        make_metric(db_session, service.id, "DOWN")

        resp = client.get(BASE)
        svc_status = resp.json()["services"][0]
        assert svc_status["uptime_last_24h"] == 75.0

    def test_avg_response_time_is_present(self, client, db_session, service):
        make_metric(db_session, service.id, "UP", response_time_ms=100.0)
        make_metric(db_session, service.id, "UP", response_time_ms=200.0)

        resp = client.get(BASE)
        avg = resp.json()["services"][0]["avg_response_time_ms"]
        assert avg == 150.0

    def test_inactive_service_excluded_from_status(self, client, db_session, service):
        db_session.query(Service).filter(Service.id == service.id).update({"is_active": False})
        db_session.commit()
        make_metric(db_session, service.id, "UP")

        resp = client.get(BASE)
        assert resp.json()["total_services"] == 0

    def test_response_includes_service_id_and_url(self, client, db_session, service):
        make_metric(db_session, service.id, "UP")

        resp = client.get(BASE)
        svc = resp.json()["services"][0]
        assert svc["id"] == service.id
        assert svc["url"] == service.url
