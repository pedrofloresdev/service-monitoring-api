"""
Endpoint tests for /api/v1/metrics/services/{id}.
Covers: pagination, time-range filter, 404 on unknown service.
"""
from datetime import datetime, timedelta, timezone

from tests.conftest import make_metric

BASE = "/api/v1/metrics/services"


class TestGetMetrics:
    def test_unknown_service_returns_404(self, client):
        resp = client.get(f"{BASE}/9999")
        assert resp.status_code == 404

    def test_empty_metrics_returns_page_with_zero_total(self, client, service):
        resp = client.get(f"{BASE}/{service.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []
        assert body["service_id"] == service.id

    def test_returns_all_metrics(self, client, db_session, service):
        for _ in range(5):
            make_metric(db_session, service.id, "UP")

        resp = client.get(f"{BASE}/{service.id}")
        assert resp.status_code == 200
        assert resp.json()["total"] == 5

    def test_limit_restricts_items_returned(self, client, db_session, service):
        for _ in range(10):
            make_metric(db_session, service.id, "UP")

        resp = client.get(f"{BASE}/{service.id}", params={"limit": 3})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 3
        assert body["total"] == 10

    def test_offset_skips_items(self, client, db_session, service):
        for _ in range(5):
            make_metric(db_session, service.id, "UP")

        resp = client.get(f"{BASE}/{service.id}", params={"limit": 10, "offset": 3})
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2

    def test_hours_filter_excludes_old_metrics(self, client, db_session, service):
        old_metric = make_metric(db_session, service.id, "UP")
        old_time = datetime.now(timezone.utc) - timedelta(hours=48)
        db_session.query(old_metric.__class__).filter_by(id=old_metric.id).update({"checked_at": old_time})
        db_session.commit()

        make_metric(db_session, service.id, "UP")

        resp = client.get(f"{BASE}/{service.id}", params={"hours": 24})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1

    def test_metrics_ordered_newest_first(self, client, db_session, service):
        for _ in range(3):
            make_metric(db_session, service.id, "UP")

        resp = client.get(f"{BASE}/{service.id}")
        items = resp.json()["items"]
        timestamps = [item["checked_at"] for item in items]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_limit_above_max_returns_422(self, client, service):
        resp = client.get(f"{BASE}/{service.id}", params={"limit": 9999})
        assert resp.status_code == 422
