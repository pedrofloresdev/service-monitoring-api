"""Compare the schema built by the migration history with the schema the models declare.

Spec 001 RF-7. Usage:

    TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres \\
        python -m tools.schema_check

It creates two disposable databases on that server: one migrated with `alembic upgrade head`,
one built with `Base.metadata.create_all`. It compares them and drops both. Exit codes:
0 = no differences, 1 = differences (or the migration failed), 2 = setup problem.

The pure functions normalize SQLAlchemy inspector output into a SchemaSnapshot and diff two
snapshots. Constraint and index names are ignored on purpose: only structure (columns,
uniqueness, foreign-key target and delete rule) is compared.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.types import TypeEngine

IGNORED_TABLES = frozenset({"alembic_version"})
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_VAR = "TEST_POSTGRES_URL"
SETUP_HELP = f"""\
{ENV_VAR} must point to a PostgreSQL server where databases can be created and dropped.
Start one locally with:
    cp .env.example .env   # if you have no .env yet (docker compose needs it)
    docker compose up -d db
    export {ENV_VAR}=postgresql://postgres:postgres@localhost:5432/postgres"""


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    default: str | None


@dataclass(frozen=True)
class ForeignKeyInfo:
    columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]
    ondelete: str | None

    def describe(self) -> str:
        rule = self.ondelete or "NO ACTION"
        return (
            f"foreign key ({', '.join(self.columns)}) -> "
            f"{self.referred_table}({', '.join(self.referred_columns)}) ON DELETE {rule}"
        )


@dataclass(frozen=True)
class TableInfo:
    columns: tuple[ColumnInfo, ...]
    primary_key: tuple[str, ...]
    unique_constraints: frozenset[tuple[str, ...]] = frozenset()
    foreign_keys: frozenset[ForeignKeyInfo] = frozenset()
    # (columns, unique)
    indexes: frozenset[tuple[tuple[str, ...], bool]] = frozenset()


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: Mapping[str, TableInfo] = field(default_factory=dict)


class InspectorLike(Protocol):
    """The subset of sqlalchemy.engine.Inspector this module reads."""

    def get_table_names(self) -> Sequence[str]: ...
    def get_columns(self, table_name: str, /) -> Sequence[Mapping[str, Any]]: ...
    def get_pk_constraint(self, table_name: str, /) -> Mapping[str, Any]: ...
    def get_unique_constraints(self, table_name: str, /) -> Sequence[Mapping[str, Any]]: ...
    def get_foreign_keys(self, table_name: str, /) -> Sequence[Mapping[str, Any]]: ...
    def get_indexes(self, table_name: str, /) -> Sequence[Mapping[str, Any]]: ...


def normalize_type(type_: TypeEngine[Any]) -> str:
    """Render a type as PostgreSQL DDL, so length, precision and time zone are kept."""
    # SQLAlchemy's PGDialect.__init__ is not annotated (library typing limitation).
    return type_.compile(dialect=PGDialect())  # type: ignore[no-untyped-call]


def snapshot_from_inspector(inspector: InspectorLike) -> SchemaSnapshot:
    tables: dict[str, TableInfo] = {}
    for name in sorted(inspector.get_table_names()):
        if name in IGNORED_TABLES:
            continue
        columns = tuple(
            ColumnInfo(
                name=c["name"],
                type=normalize_type(c["type"]),
                nullable=bool(c["nullable"]),
                default=c.get("default"),
            )
            for c in inspector.get_columns(name)
        )
        primary_key = tuple(inspector.get_pk_constraint(name).get("constrained_columns") or ())
        unique = frozenset(
            tuple(sorted(u["column_names"])) for u in inspector.get_unique_constraints(name)
        )
        foreign_keys = frozenset(
            ForeignKeyInfo(
                columns=tuple(fk["constrained_columns"]),
                referred_table=fk["referred_table"],
                referred_columns=tuple(fk["referred_columns"]),
                ondelete=(fk.get("options") or {}).get("ondelete"),
            )
            for fk in inspector.get_foreign_keys(name)
        )
        indexes = frozenset(
            (tuple(ix["column_names"]), bool(ix["unique"]))
            for ix in inspector.get_indexes(name)
            if not ix.get("duplicates_constraint")
        )
        tables[name] = TableInfo(columns, primary_key, unique, foreign_keys, indexes)
    return SchemaSnapshot(tables)


def _cols(columns: Sequence[str]) -> str:
    return f"({', '.join(columns)})"


def _diff_sets(
    table: str, kind: str, migrated: frozenset[str], models: frozenset[str]
) -> list[str]:
    diffs = [f"{table}: {kind}{d} in models but not in migrated schema" for d in models - migrated]
    diffs += [f"{table}: {kind}{d} in migrated schema but not in models" for d in migrated - models]
    return diffs


def _diff_table(name: str, migrated: TableInfo, models: TableInfo) -> list[str]:
    diffs: list[str] = []
    m_cols = {c.name: c for c in migrated.columns}
    d_cols = {c.name: c for c in models.columns}
    for col in d_cols.keys() - m_cols.keys():
        diffs.append(f"{name}.{col}: column in models but not in migrated schema")
    for col in m_cols.keys() - d_cols.keys():
        diffs.append(f"{name}.{col}: column in migrated schema but not in models")
    for col in m_cols.keys() & d_cols.keys():
        a, b = m_cols[col], d_cols[col]
        if a.type != b.type:
            diffs.append(f"{name}.{col}: type {a.type} (migrated) != {b.type} (models)")
        if a.nullable != b.nullable:
            diffs.append(f"{name}.{col}: nullable {a.nullable} (migrated) != {b.nullable} (models)")
        if a.default != b.default:
            diffs.append(
                f"{name}.{col}: default {a.default!r} (migrated) != {b.default!r} (models)"
            )

    if migrated.primary_key != models.primary_key:
        diffs.append(
            f"{name}: primary key {_cols(migrated.primary_key)} (migrated) "
            f"!= {_cols(models.primary_key)} (models)"
        )
    diffs += _diff_sets(
        name,
        "unique constraint ",
        frozenset(_cols(u) for u in migrated.unique_constraints),
        frozenset(_cols(u) for u in models.unique_constraints),
    )
    diffs += _diff_sets(
        name,
        "",
        frozenset(fk.describe() for fk in migrated.foreign_keys),
        frozenset(fk.describe() for fk in models.foreign_keys),
    )
    diffs += _diff_sets(
        name,
        "",
        frozenset(f"{'unique index' if u else 'index'} {_cols(c)}" for c, u in migrated.indexes),
        frozenset(f"{'unique index' if u else 'index'} {_cols(c)}" for c, u in models.indexes),
    )
    return diffs


def diff_schemas(migrated: SchemaSnapshot, models: SchemaSnapshot) -> list[str]:
    """One human-readable line per difference, sorted for deterministic output."""
    diffs: list[str] = []
    for table in models.tables.keys() - migrated.tables.keys():
        diffs.append(f"table {table}: in models but not in migrated schema")
    for table in migrated.tables.keys() - models.tables.keys():
        diffs.append(f"table {table}: in migrated schema but not in models")
    for table in migrated.tables.keys() & models.tables.keys():
        diffs += _diff_table(table, migrated.tables[table], models.tables[table])
    return sorted(diffs)


def format_differences(diffs: Sequence[str]) -> str:
    if not diffs:
        return "No schema differences between the migration history and the models."
    lines = [f"{len(diffs)} schema difference(s) between the migration history and the models:"]
    lines += [f"  - {d}" for d in diffs]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# I/O: disposable databases, Alembic, entry point
# ---------------------------------------------------------------------------


@contextmanager
def temporary_database(admin_url: str, prefix: str) -> Iterator[str]:
    """Create a uniquely named database and always drop it on exit. Yields its URL."""
    name = f"{prefix}_{secrets.token_hex(4)}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            yield make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        finally:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


def run_alembic(args: Sequence[str], database_url: str) -> subprocess.CompletedProcess[str]:
    """Run Alembic in a subprocess against `database_url` (migrations/env.py reads DATABASE_URL)."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )


def snapshot_database(database_url: str) -> SchemaSnapshot:
    engine = create_engine(database_url)
    try:
        return snapshot_from_inspector(inspect(engine))
    finally:
        engine.dispose()


def build_model_schema(database_url: str) -> None:
    # Imported here so the pure functions above stay importable without the app.
    import app.db.models.metrics  # noqa: F401
    import app.db.models.services  # noqa: F401
    from app.db.base import Base

    engine = create_engine(database_url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


def main() -> int:
    admin_url = os.environ.get(ENV_VAR)
    if not admin_url:
        print(SETUP_HELP, file=sys.stderr)
        return 2
    try:
        engine = create_engine(admin_url)
        with engine.connect():
            pass
        engine.dispose()
    except SQLAlchemyError as exc:
        print(
            f"Cannot connect to {ENV_VAR}: {exc.__class__.__name__}\n\n{SETUP_HELP}",
            file=sys.stderr,
        )
        return 2

    with (
        temporary_database(admin_url, "schema_check_migrated") as migrated_url,
        temporary_database(admin_url, "schema_check_models") as models_url,
    ):
        result = run_alembic(["upgrade", "head"], migrated_url)
        if result.returncode != 0:
            print("alembic upgrade head failed on an empty database:", file=sys.stderr)
            print(result.stdout + result.stderr, file=sys.stderr)
            return 1
        build_model_schema(models_url)
        diffs = diff_schemas(snapshot_database(migrated_url), snapshot_database(models_url))

    print(format_differences(diffs))
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
