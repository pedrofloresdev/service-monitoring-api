"""Spec 003a RNF-2: the services.origin migration on PostgreSQL."""

from collections.abc import Callable

import pytest
from sqlalchemy import create_engine, text

from tests.postgres.conftest import AlembicRunner

pytestmark = pytest.mark.postgres

PRE_003A_REVISION = "a62134b6f191"


def _scalar_rows(url: str, query: str) -> list[tuple[object, ...]]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(query))]
    finally:
        engine.dispose()


def _columns(url: str) -> set[str]:
    return {
        str(r[0])
        for r in _scalar_rows(
            url,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'services'",
        )
    }


def test_existing_services_become_ordinary_and_downgrade_removes_origin(
    make_database: Callable[[], str], alembic: AlembicRunner
) -> None:
    url = make_database()
    assert alembic(url, "upgrade", PRE_003A_REVISION).returncode == 0
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO services (name, url, expected_status, check_interval, is_active) "
                "VALUES ('a', 'https://a.example/', 200, 60, true), "
                "('b', 'https://b.example/', 200, 60, false)"
            )
        )
    engine.dispose()

    upgrade = alembic(url, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.output
    assert _scalar_rows(url, "SELECT origin FROM services ORDER BY id") == [
        ("ordinary",),
        ("ordinary",),
    ]

    rejected = create_engine(url)
    with pytest.raises(Exception, match="ck_services_origin"):
        with rejected.begin() as conn:
            conn.execute(text("UPDATE services SET origin = 'admin'"))
    rejected.dispose()

    downgrade = alembic(url, "downgrade", PRE_003A_REVISION)
    assert downgrade.returncode == 0, downgrade.output
    assert "origin" not in _columns(url)
    assert _scalar_rows(url, "SELECT count(*) FROM services") == [(2,)]
