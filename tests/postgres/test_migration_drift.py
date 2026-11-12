"""
Spec 001: the migration that aligns the migration history with the models.
Each test uses its own disposable PostgreSQL database, seeded at the pre-001 revision.
"""

from collections.abc import Callable

import pytest
from sqlalchemy import create_engine, text

from tests.postgres.conftest import PRE_001_REVISION, AlembicRunner
from tools.schema_check import build_model_schema, diff_schemas, snapshot_database

pytestmark = pytest.mark.postgres

MakeDatabase = Callable[[], str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _execute(url: str, *statements: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    finally:
        engine.dispose()


def _scalar(url: str, query: str) -> object:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(query)).scalar()
    finally:
        engine.dispose()


def _rows(url: str, query: str) -> list[tuple[object, ...]]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(query))]
    finally:
        engine.dispose()


def _upgrade(alembic: AlembicRunner, url: str, revision: str = "head") -> None:
    result = alembic(url, "upgrade", revision)
    assert result.returncode == 0, result.output


SEED_SERVICES = (
    "INSERT INTO services (id, name, url, expected_status, check_interval, is_active) VALUES "
    "(1, 'nulls', 'http://nulls.example', NULL, NULL, NULL), "
    "(2, 'set', 'http://set.example', 201, 30, false)"
)
SEED_VALID_METRICS = (
    "INSERT INTO metrics (service_id, status, status_code, response_time_ms, checked_at) VALUES "
    "(2, 'UP', 201, 10.5, now()), (2, 'DOWN', 500, 20.0, now())"
)
SEED_LEGACY_METRICS = (
    "INSERT INTO metrics (service_id, status, status_code, response_time_ms, checked_at) VALUES "
    "(2, 'UP', 201, 1.0, NULL), (2, 'UP', 201, 2.0, NULL), (2, 'DOWN', 500, 3.0, NULL)"
)


def _seeded_pre_001_database(make_database: MakeDatabase, alembic: AlembicRunner) -> str:
    url = make_database()
    _upgrade(alembic, url, PRE_001_REVISION)
    _execute(url, SEED_SERVICES, SEED_VALID_METRICS, SEED_LEGACY_METRICS)
    return url


def _model_snapshot_diff(make_database: MakeDatabase, migrated_url: str) -> list[str]:
    models_url = make_database()
    build_model_schema(models_url)
    return diff_schemas(snapshot_database(migrated_url), snapshot_database(models_url))


# ---------------------------------------------------------------------------
# RF-1: full history matches the models
# ---------------------------------------------------------------------------


def test_full_history_matches_models(make_database: MakeDatabase, alembic: AlembicRunner) -> None:
    url = make_database()
    _upgrade(alembic, url)
    assert _model_snapshot_diff(make_database, url) == []


# ---------------------------------------------------------------------------
# RF-3: legacy metrics (NULL checked_at) are deleted and counted
# ---------------------------------------------------------------------------


def test_legacy_metrics_deleted_and_counted(
    make_database: MakeDatabase, alembic: AlembicRunner
) -> None:
    url = _seeded_pre_001_database(make_database, alembic)

    result = alembic(url, "upgrade", "head")

    assert result.returncode == 0, result.output
    assert "Deleted 3 legacy metric(s) with NULL checked_at" in result.output
    assert _scalar(url, "SELECT count(*) FROM metrics") == 2
    assert _scalar(url, "SELECT count(*) FROM metrics WHERE checked_at IS NULL") == 0


def test_zero_legacy_metrics_reports_zero(
    make_database: MakeDatabase, alembic: AlembicRunner
) -> None:
    url = make_database()
    _upgrade(alembic, url, PRE_001_REVISION)
    _execute(url, SEED_SERVICES, SEED_VALID_METRICS)

    result = alembic(url, "upgrade", "head")

    assert result.returncode == 0, result.output
    assert "Deleted 0 legacy metric(s) with NULL checked_at" in result.output
    assert _scalar(url, "SELECT count(*) FROM metrics") == 2


# ---------------------------------------------------------------------------
# RF-4: NULL service config gets the model defaults
# ---------------------------------------------------------------------------


def test_service_nulls_backfilled_with_model_defaults(
    make_database: MakeDatabase, alembic: AlembicRunner
) -> None:
    url = _seeded_pre_001_database(make_database, alembic)

    _upgrade(alembic, url)

    rows = _rows(
        url, "SELECT id, expected_status, check_interval, is_active FROM services ORDER BY id"
    )
    assert rows == [(1, 200, 60, True), (2, 201, 30, False)]


# ---------------------------------------------------------------------------
# RF-5: NULL or over-long status aborts and changes nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_status", ["NULL", "'BROKEN'"], ids=["null-status", "long-status"])
def test_bad_status_aborts_and_changes_nothing(
    make_database: MakeDatabase, alembic: AlembicRunner, bad_status: str
) -> None:
    url = _seeded_pre_001_database(make_database, alembic)
    _execute(
        url,
        f"INSERT INTO metrics (service_id, status, checked_at) VALUES (2, {bad_status}, now())",
    )
    before = snapshot_database(url)

    result = alembic(url, "upgrade", "head")

    assert result.returncode != 0
    assert "1 metrics row(s) have a NULL or >4-character status" in result.output
    assert _scalar(url, "SELECT version_num FROM alembic_version") == PRE_001_REVISION
    assert snapshot_database(url) == before
    # Legacy metrics are still there: the delete was rolled back.
    assert _scalar(url, "SELECT count(*) FROM metrics") == 6


# ---------------------------------------------------------------------------
# RF-6: downgrade restores definitions and keeps rows
# ---------------------------------------------------------------------------


def test_downgrade_restores_definitions_and_keeps_rows(
    make_database: MakeDatabase, alembic: AlembicRunner
) -> None:
    reference = make_database()
    _upgrade(alembic, reference, PRE_001_REVISION)

    url = _seeded_pre_001_database(make_database, alembic)
    _upgrade(alembic, url)
    result = alembic(url, "downgrade", PRE_001_REVISION)

    assert result.returncode == 0, result.output
    assert snapshot_database(url) == snapshot_database(reference)
    assert _scalar(url, "SELECT count(*) FROM metrics") == 2
    assert _scalar(url, "SELECT count(*) FROM services") == 2


def test_downgrade_then_upgrade_equals_single_upgrade(
    make_database: MakeDatabase, alembic: AlembicRunner
) -> None:
    once = make_database()
    _upgrade(alembic, once)

    round_trip = make_database()
    _upgrade(alembic, round_trip)
    downgrade = alembic(round_trip, "downgrade", PRE_001_REVISION)
    assert downgrade.returncode == 0, downgrade.output
    _upgrade(alembic, round_trip)

    assert snapshot_database(round_trip) == snapshot_database(once)
    assert _model_snapshot_diff(make_database, round_trip) == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_upgrade_succeeds_when_columns_already_fixed(
    make_database: MakeDatabase, alembic: AlembicRunner
) -> None:
    url = make_database()
    _upgrade(alembic, url, PRE_001_REVISION)
    # An operator already fixed some columns by hand.
    _execute(
        url,
        "ALTER TABLE metrics ALTER COLUMN checked_at SET DEFAULT now(), "
        "ALTER COLUMN checked_at SET NOT NULL",
        "ALTER TABLE metrics ALTER COLUMN status TYPE VARCHAR(4)",
        "ALTER TABLE services ALTER COLUMN is_active SET NOT NULL",
    )

    _upgrade(alembic, url)

    assert _model_snapshot_diff(make_database, url) == []


def test_upgrade_head_twice_is_noop(make_database: MakeDatabase, alembic: AlembicRunner) -> None:
    url = make_database()
    _upgrade(alembic, url)
    before = snapshot_database(url)

    second = alembic(url, "upgrade", "head")

    assert second.returncode == 0, second.output
    assert "Running upgrade" not in second.output
    assert snapshot_database(url) == before
