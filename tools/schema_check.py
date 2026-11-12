"""Compare the schema built by the migration history with the schema the models declare.

Spec 001 RF-7. The pure functions here normalize SQLAlchemy inspector output into a
SchemaSnapshot and diff two snapshots. Constraint and index names are ignored on purpose:
only structure (columns, uniqueness, foreign-key target and delete rule) is compared.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.types import TypeEngine

IGNORED_TABLES = frozenset({"alembic_version"})


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
