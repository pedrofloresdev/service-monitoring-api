"""Fixtures for tests that need a real PostgreSQL server (spec 001).

These tests are marked `postgres` and deselected by default. When selected without
TEST_POSTGRES_URL they ERROR with setup instructions instead of being skipped, so a
missing database can never look like a passing run.
"""

import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass

import pytest

from tools.schema_check import ENV_VAR, SETUP_HELP, run_alembic, temporary_database

# Revision before spec 001's migration: the schema with the drift.
PRE_001_REVISION = "3f8a12b9c4d1"


@dataclass(frozen=True)
class AlembicResult:
    returncode: int
    output: str


AlembicRunner = Callable[..., AlembicResult]


@pytest.fixture(scope="session")
def pg_admin_url() -> str:
    url = os.environ.get(ENV_VAR)
    if not url:
        pytest.fail(SETUP_HELP, pytrace=False)
    return url


@pytest.fixture
def make_database(pg_admin_url: str) -> Iterator[Callable[[], str]]:
    """Factory: each call creates an empty, uniquely named database, dropped after the test."""
    with ExitStack() as stack:

        def _make() -> str:
            return stack.enter_context(temporary_database(pg_admin_url, "pytest_pg"))

        yield _make


@pytest.fixture
def alembic() -> AlembicRunner:
    """Run an Alembic command against a database URL: alembic(url, "upgrade", "head")."""

    def _run(database_url: str, *args: str) -> AlembicResult:
        result = run_alembic(args, database_url)
        return AlembicResult(result.returncode, result.stdout + result.stderr)

    return _run
