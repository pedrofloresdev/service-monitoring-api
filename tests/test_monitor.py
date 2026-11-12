"""
Unit tests for the monitor service layer.
Tests check_service (HTTP logic) and get_consecutive_failures (DB logic)
in isolation — no real HTTP calls, mocked via httpx.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.db.models.metrics import Metric
from app.db.models.services import Service
from app.services.monitor import check_service, check_service_by_id, get_consecutive_failures
from app.services.url_safety import (
    RESOLUTION_FAILED_PREFIX,
    UNSAFE_LITERAL_MESSAGE,
    UNSAFE_RESOLVED_MESSAGE,
)
from tests.conftest import FakeResolver, make_metric


def _fake_service(url: str = "https://example.com/health", expected_status: int = 200) -> Service:
    # Duck-typed stand-in: check_service only reads url and expected_status.
    return cast(Service, SimpleNamespace(url=url, expected_status=expected_status))


# ---------------------------------------------------------------------------
# check_service
# ---------------------------------------------------------------------------


class TestCheckService:
    def test_returns_up_when_status_matches_expected(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_response
            result = check_service(_fake_service(expected_status=200))

        assert result["status"] == "UP"
        assert result["status_code"] == 200
        assert result["response_time_ms"] is not None
        assert result["error_message"] is None

    def test_returns_down_when_status_does_not_match(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_response
            result = check_service(_fake_service(expected_status=200))

        assert result["status"] == "DOWN"
        assert result["status_code"] == 500
        assert result["error_message"] is None

    def test_returns_down_on_connection_error(self) -> None:
        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.side_effect = (
                httpx.ConnectError("Connection refused")
            )
            result = check_service(_fake_service())

        assert result["status"] == "DOWN"
        assert result["status_code"] is None
        assert result["response_time_ms"] is None
        assert "Connection refused" in result["error_message"]

    def test_returns_down_on_timeout(self) -> None:
        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.side_effect = (
                httpx.TimeoutException("timed out")
            )
            result = check_service(_fake_service())

        assert result["status"] == "DOWN"
        assert result["response_time_ms"] is None

    def test_response_time_is_float(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.services.monitor.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_response
            result = check_service(_fake_service())

        assert isinstance(result["response_time_ms"], float)

    def test_non_default_expected_status_is_respected(self) -> None:
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
    def test_returns_zero_when_no_metrics(self, db_session: Session, service: Service) -> None:
        count = get_consecutive_failures(db_session, service.id)
        assert count == 0

    def test_returns_zero_when_last_metric_is_up(
        self, db_session: Session, service: Service
    ) -> None:
        make_metric(db_session, service.id, "DOWN")
        make_metric(db_session, service.id, "UP")
        assert get_consecutive_failures(db_session, service.id) == 0

    def test_counts_consecutive_failures_from_most_recent(
        self, db_session: Session, service: Service
    ) -> None:
        make_metric(db_session, service.id, "UP")
        make_metric(db_session, service.id, "DOWN")
        make_metric(db_session, service.id, "DOWN")
        assert get_consecutive_failures(db_session, service.id) == 2

    def test_stops_counting_at_first_up(self, db_session: Session, service: Service) -> None:
        make_metric(db_session, service.id, "DOWN")
        make_metric(db_session, service.id, "UP")
        make_metric(db_session, service.id, "DOWN")
        make_metric(db_session, service.id, "DOWN")
        # Latest two are DOWN, then UP breaks the streak
        assert get_consecutive_failures(db_session, service.id) == 2

    def test_caps_at_default_limit_when_all_are_failures(
        self, db_session: Session, service: Service
    ) -> None:
        # Default limit == FAIL_THRESHOLD (3), so only last 3 are inspected.
        for _ in range(5):
            make_metric(db_session, service.id, "DOWN")
        assert get_consecutive_failures(db_session, service.id) == 3

    def test_returns_full_count_when_explicit_limit_is_larger(
        self, db_session: Session, service: Service
    ) -> None:
        for _ in range(5):
            make_metric(db_session, service.id, "DOWN")
        assert get_consecutive_failures(db_session, service.id, limit=10) == 5

    def test_respects_limit_parameter(self, db_session: Session, service: Service) -> None:
        for _ in range(10):
            make_metric(db_session, service.id, "DOWN")
        # With limit=3, only checks 3 most recent
        assert get_consecutive_failures(db_session, service.id, limit=3) == 3


# ---------------------------------------------------------------------------
# URL safety at check time (spec 002 RF-6 to RF-9, RF-16)
# ---------------------------------------------------------------------------


def _assert_failed_without_request(result: dict[str, Any], client_cls: MagicMock) -> None:
    assert result["status"] == "DOWN"
    assert result["status_code"] is None
    assert result["response_time_ms"] is None
    client_cls.assert_not_called()


class TestCheckTimeDestination:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/",
            "http://localhost./",
            "http://localhost../",
            "http://app.localhost:8000/x",
            "http://127.0.0.1/",
            "http://10.0.0.1:8443/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://[::ffff:7f00:1]/",
        ],
    )
    def test_reserved_name_or_unsafe_literal_is_blocked(
        self, fake_resolver: FakeResolver, url: str
    ) -> None:
        with patch("app.services.monitor.httpx.Client") as client_cls:
            result = check_service(_fake_service(url=url))

        _assert_failed_without_request(result, client_cls)
        assert result["error_message"] == UNSAFE_LITERAL_MESSAGE
        assert fake_resolver.calls == []

    @pytest.mark.parametrize(
        "addresses", [("10.0.0.5",), ("93.184.216.34", "10.0.0.5"), ("2606:4700::1", "fe80::1")]
    )
    def test_hostname_resolving_to_non_public_is_blocked(
        self, fake_resolver: FakeResolver, addresses: tuple[str, ...]
    ) -> None:
        fake_resolver.set("internal.test", *addresses)

        with patch("app.services.monitor.httpx.Client") as client_cls:
            result = check_service(_fake_service(url="https://internal.test/health"))

        _assert_failed_without_request(result, client_cls)
        assert result["error_message"] == UNSAFE_RESOLVED_MESSAGE
        assert fake_resolver.calls == ["internal.test"]

    @pytest.mark.parametrize("error", ["name not found", "timed out", "lookup failed"])
    def test_unresolvable_hostname_fails_without_request(
        self, fake_resolver: FakeResolver, error: str
    ) -> None:
        fake_resolver.set("gone.test", error=error)

        with patch("app.services.monitor.httpx.Client") as client_cls:
            result = check_service(_fake_service(url="https://gone.test/"))

        _assert_failed_without_request(result, client_cls)
        assert result["error_message"] == f"{RESOLUTION_FAILED_PREFIX} gone.test: {error}"

    def test_hostname_that_turns_private_is_blocked_at_the_next_check(
        self, fake_resolver: FakeResolver
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        service = _fake_service(url="https://rebind.test/")

        with patch("app.services.monitor.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.get.return_value = mock_response
            first = check_service(service)
            fake_resolver.set("rebind.test", "127.0.0.1")
            second = check_service(service)

        assert first["status"] == "UP"
        assert second["status"] == "DOWN"
        assert second["error_message"] == UNSAFE_RESOLVED_MESSAGE
        assert client_cls.call_count == 1

    def test_stored_unsafe_service_is_kept_and_its_check_is_blocked(
        self, db_session: Session
    ) -> None:
        svc = Service(
            name="Legacy", url="http://localhost/", expected_status=200, check_interval=60
        )
        db_session.add(svc)
        db_session.commit()
        earlier = make_metric(db_session, svc.id, "UP")
        service_id, earlier_id = svc.id, earlier.id

        with (
            patch("app.services.monitor.SessionLocal", return_value=db_session),
            patch("app.services.monitor.httpx.Client") as client_cls,
        ):
            check_service_by_id(service_id)

        client_cls.assert_not_called()
        stored = db_session.get(Service, service_id)
        assert stored is not None
        assert (stored.url, stored.is_active) == ("http://localhost/", True)
        metrics = db_session.query(Metric).filter(Metric.service_id == service_id).all()
        assert [m.id for m in metrics if m.id == earlier_id] == [earlier_id]
        latest = max(metrics, key=lambda m: m.id)
        assert (latest.status, latest.error_message) == ("DOWN", UNSAFE_LITERAL_MESSAGE)
