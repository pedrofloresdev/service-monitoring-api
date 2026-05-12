"""
Unit tests for the monitor service layer.
Tests check_service (HTTP logic) and get_consecutive_failures (DB logic)
in isolation — no real HTTP calls, mocked via httpx.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from app.services.monitor import check_service, get_consecutive_failures
from tests.conftest import make_metric


def _fake_service(url="https://example.com/health", expected_status=200):
    return SimpleNamespace(url=url, expected_status=expected_status)


# ---------------------------------------------------------------------------
# check_service
# ---------------------------------------------------------------------------

class TestCheckService:
    def test_returns_up_when_status_matches_expected(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_response
            result = check_service(_fake_service(expected_status=200))

        assert result["status"] == "UP"
        assert result["status_code"] == 200
        assert result["response_time_ms"] is not None
        assert result["error_message"] is None

    def test_returns_down_when_status_does_not_match(self):
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_response
            result = check_service(_fake_service(expected_status=200))

        assert result["status"] == "DOWN"
        assert result["status_code"] == 500
        assert result["error_message"] is None

    def test_returns_down_on_connection_error(self):
        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.side_effect = (
                httpx.ConnectError("Connection refused")
            )
            result = check_service(_fake_service())

        assert result["status"] == "DOWN"
        assert result["status_code"] is None
        assert result["response_time_ms"] is None
        assert "Connection refused" in result["error_message"]

    def test_returns_down_on_timeout(self):
        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.side_effect = (
                httpx.TimeoutException("timed out")
            )
            result = check_service(_fake_service())

        assert result["status"] == "DOWN"
        assert result["response_time_ms"] is None

    def test_response_time_is_float(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_response
            result = check_service(_fake_service())

        assert isinstance(result["response_time_ms"], float)

    def test_non_default_expected_status_is_respected(self):
        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_response
            result = check_service(_fake_service(expected_status=201))

        assert result["status"] == "UP"


# ---------------------------------------------------------------------------
# get_consecutive_failures
# ---------------------------------------------------------------------------

class TestGetConsecutiveFailures:
    def test_returns_zero_when_no_metrics(self, db_session, service):
        count = get_consecutive_failures(db_session, service.id)
        assert count == 0

    def test_returns_zero_when_last_metric_is_up(self, db_session, service):
        make_metric(db_session, service.id, "DOWN")
        make_metric(db_session, service.id, "UP")
        assert get_consecutive_failures(db_session, service.id) == 0

    def test_counts_consecutive_failures_from_most_recent(self, db_session, service):
        make_metric(db_session, service.id, "UP")
        make_metric(db_session, service.id, "DOWN")
        make_metric(db_session, service.id, "DOWN")
        assert get_consecutive_failures(db_session, service.id) == 2

    def test_stops_counting_at_first_up(self, db_session, service):
        make_metric(db_session, service.id, "DOWN")
        make_metric(db_session, service.id, "UP")
        make_metric(db_session, service.id, "DOWN")
        make_metric(db_session, service.id, "DOWN")
        # Latest two are DOWN, then UP breaks the streak
        assert get_consecutive_failures(db_session, service.id) == 2

    def test_caps_at_default_limit_when_all_are_failures(self, db_session, service):
        # Default limit == FAIL_THRESHOLD (3), so only last 3 are inspected.
        for _ in range(5):
            make_metric(db_session, service.id, "DOWN")
        assert get_consecutive_failures(db_session, service.id) == 3

    def test_returns_full_count_when_explicit_limit_is_larger(self, db_session, service):
        for _ in range(5):
            make_metric(db_session, service.id, "DOWN")
        assert get_consecutive_failures(db_session, service.id, limit=10) == 5

    def test_respects_limit_parameter(self, db_session, service):
        for _ in range(10):
            make_metric(db_session, service.id, "DOWN")
        # With limit=3, only checks 3 most recent
        assert get_consecutive_failures(db_session, service.id, limit=3) == 3
