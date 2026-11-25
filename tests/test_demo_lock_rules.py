"""Pure rules of the start command and the demo lock (spec 003d RF-3, RF-5, RF-6)."""

import pytest

from app.services.demo_lock_rules import (
    APPLICATION_NAME,
    CONNECT_TIMEOUT_SECONDS,
    classify_connect_error,
    connect_kwargs,
    keepalive_settings,
    lock_database,
    lock_key,
    merge_options,
    parse_port,
    parse_web_concurrency,
    public_reason,
    split_key,
)

# ---------------------------------------------------------------------------
# RF-3: WEB_CONCURRENCY, and PORT
# ---------------------------------------------------------------------------


class TestWebConcurrency:
    @pytest.mark.parametrize(("raw", "expected"), [(None, 1), ("", 1), ("1", 1), ("2", 2)])
    def test_valid(self, raw: str | None, expected: int) -> None:
        assert parse_web_concurrency(raw) == expected

    @pytest.mark.parametrize("raw", ["0", "+1", "1.0", " 1", "1 ", "-1", "٣", "one"])
    def test_invalid(self, raw: str) -> None:
        with pytest.raises(
            ValueError, match="^WEB_CONCURRENCY: must be a whole number of at least 1$"
        ):
            parse_web_concurrency(raw)


class TestPort:
    @pytest.mark.parametrize(
        ("raw", "expected"), [(None, 8000), ("", 8000), ("1", 1), ("65535", 65535)]
    )
    def test_valid(self, raw: str | None, expected: int) -> None:
        assert parse_port(raw) == expected

    @pytest.mark.parametrize("raw", ["0", "65536", "+80", "80.0", " 80", "http"])
    def test_invalid(self, raw: str) -> None:
        with pytest.raises(ValueError, match="^PORT: must be a whole number from 1 to 65535$"):
            parse_port(raw)


# ---------------------------------------------------------------------------
# RF-5: lock identity
# ---------------------------------------------------------------------------


class TestLockKey:
    def test_is_deterministic_and_signed_64_bit(self) -> None:
        key = lock_key("service-monitoring-api-demo", "app")
        assert key == lock_key("service-monitoring-api-demo", "app")
        assert -(2**63) <= key < 2**63

    def test_differs_by_name_and_by_database(self) -> None:
        keys = {
            lock_key("demo", "app"),
            lock_key("staging", "app"),
            lock_key("demo", "other"),
            lock_key("de", "moapp"),  # the separator keeps "de"+"moapp" apart from "demo"+"app"
        }
        assert len(keys) == 4

    @pytest.mark.parametrize("key", [0, 1, -1, 2**63 - 1, -(2**63), -0x1234567876543210])
    def test_split_matches_pg_locks_columns(self, key: int) -> None:
        classid, objid = split_key(key)
        assert 0 <= classid < 2**32 and 0 <= objid < 2**32
        rebuilt = (classid << 32) | objid
        assert (rebuilt - 2**64 if rebuilt >= 2**63 else rebuilt) == key

    def test_split_measured_on_postgresql(self) -> None:
        # Plan §1: pg_locks showed classid 3989547399, objid 2309737968 for this key.
        assert split_key(-0x1234567876543210) == (3989547399, 2309737968)

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("postgresql://u:p@db:5432/app", "app"),
            ("postgresql+psycopg2://u:p@db/app?sslmode=require", "app"),
            ("postgresql://u:p@db:5432", "u"),  # libpq's default database is the user name
        ],
    )
    def test_lock_database(self, url: str, expected: str) -> None:
        assert lock_database(url) == expected


# ---------------------------------------------------------------------------
# RF-6: dead-connection detection and connection options
# ---------------------------------------------------------------------------


class TestKeepalive:
    @pytest.mark.parametrize(
        ("k", "expected"), [(10, (3, 1, 3)), (30, (10, 3, 3)), (600, (200, 66, 3))]
    )
    def test_values(self, k: int, expected: tuple[int, int, int]) -> None:
        assert keepalive_settings(k) == expected

    def test_detection_is_below_k_for_every_valid_k(self) -> None:
        for k in range(10, 601):
            idle, interval, count = keepalive_settings(k)
            assert interval >= 1 and idle + count * interval < k, k


class TestConnectionOptions:
    def test_merge_options_puts_ours_last(self) -> None:
        merged = merge_options(
            "-c work_mem=8MB -c tcp_keepalives_idle=99", {"tcp_keepalives_idle": 7}
        )
        assert merged == "-c work_mem=8MB -c tcp_keepalives_idle=99 -c tcp_keepalives_idle=7"

    def test_merge_options_without_existing(self) -> None:
        assert merge_options(None, {"a": 1, "b": 2}) == "-c a=1 -c b=2"

    def test_connect_kwargs_keeps_query_and_lock_options_win(self) -> None:
        kwargs = connect_kwargs(
            "postgresql+psycopg2://user:p%40ss@db:6543/app"
            "?sslmode=require&connect_timeout=30&application_name=x&options=-c%20work_mem%3D8MB",
            keepalive_k=30,
        )
        assert kwargs == {
            "host": "db",
            "port": 6543,
            "user": "user",
            "password": "p@ss",
            "dbname": "app",
            "sslmode": "require",
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "application_name": APPLICATION_NAME,
            "options": "-c work_mem=8MB -c tcp_keepalives_idle=10"
            " -c tcp_keepalives_interval=3 -c tcp_keepalives_count=3",
        }

    def test_connect_kwargs_omits_missing_parts(self) -> None:
        kwargs = connect_kwargs("postgresql:///app?host=/var/run/postgresql", keepalive_k=10)
        assert kwargs["host"] == "/var/run/postgresql" and kwargs["dbname"] == "app"
        assert "port" not in kwargs and "user" not in kwargs and "password" not in kwargs


# ---------------------------------------------------------------------------
# RF-5: connection errors (messages measured on PostgreSQL 15, plan §1)
# ---------------------------------------------------------------------------

PREFIX = 'connection to server at "localhost" (127.0.0.1), port 5432 failed: '


class TestConnectErrors:
    @pytest.mark.parametrize(
        "message",
        [
            PREFIX.replace("5432", "5499") + "Connection refused\n\tIs the server running?",
            'connection to server at "10.255.255.1", port 5432 failed: timeout expired',
            'could not translate host name "nohost.invalid" to address: Name or service not known',
            'could not translate host name "db" to address: Temporary failure in name resolution',
            PREFIX + "FATAL:  the database system is starting up",
            PREFIX + "FATAL:  the database system is shutting down",
            PREFIX + "FATAL:  sorry, too many clients already",
            PREFIX + "No route to host",
            'connection to server on socket "/run/postgresql/.s.PGSQL.5432" failed: '
            "No such file or directory",
        ],
    )
    def test_unreachable(self, message: str) -> None:
        assert classify_connect_error(message) == "unreachable"

    @pytest.mark.parametrize(
        "message",
        [
            PREFIX + 'FATAL:  password authentication failed for user "postgres"',
            PREFIX + 'FATAL:  database "nodb" does not exist',
            PREFIX + 'FATAL:  no pg_hba.conf entry for host "1.2.3.4", user "u", database "d"',
            PREFIX + "server does not support SSL, but SSL was required",
        ],
    )
    def test_permanent(self, message: str) -> None:
        assert classify_connect_error(message) == "permanent"

    def test_public_reason_is_the_first_line(self) -> None:
        message = PREFIX + 'FATAL:  database "nodb" does not exist\n  detail'
        assert public_reason(message, "pw") == PREFIX + 'FATAL:  database "nodb" does not exist'

    def test_public_reason_removes_the_password(self) -> None:
        message = "failed for postgresql://u:s3cr%2Ft@db and s3cr/t "
        assert public_reason(message, "s3cr/t") == "failed for postgresql://u:***@db and ***"

    def test_public_reason_without_password(self) -> None:
        assert public_reason("  boom  ", None) == "boom"
