"""
Unit tests for the schema comparison used by `python -m tools.schema_check` (spec 001 RF-7).
Pure logic only: no PostgreSQL needed. Inspector data is faked.
"""

from typing import Any

import pytest
from sqlalchemy import TIMESTAMP, VARCHAR, Boolean, Integer
from sqlalchemy.types import TypeEngine

from tools.schema_check import (
    ColumnInfo,
    ForeignKeyInfo,
    SchemaSnapshot,
    TableInfo,
    diff_schemas,
    format_differences,
    main,
    normalize_type,
    snapshot_from_inspector,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table(
    columns: list[tuple[str, str, bool, str | None]],
    primary_key: tuple[str, ...] = ("id",),
    unique: frozenset[tuple[str, ...]] = frozenset(),
    foreign_keys: frozenset[ForeignKeyInfo] = frozenset(),
    indexes: frozenset[tuple[tuple[str, ...], bool]] = frozenset(),
) -> TableInfo:
    return TableInfo(
        columns=tuple(ColumnInfo(n, t, nullable, d) for n, t, nullable, d in columns),
        primary_key=primary_key,
        unique_constraints=unique,
        foreign_keys=foreign_keys,
        indexes=indexes,
    )


BASE_COLUMNS: list[tuple[str, str, bool, str | None]] = [
    ("id", "INTEGER", False, None),
    ("status", "VARCHAR(4)", False, None),
    ("checked_at", "TIMESTAMP WITH TIME ZONE", False, "now()"),
]


class FakeInspector:
    """Mimics the subset of sqlalchemy.engine.Inspector used by snapshot_from_inspector."""

    def __init__(self, tables: dict[str, dict[str, Any]]) -> None:
        self._tables = tables

    def get_table_names(self) -> list[str]:
        return list(self._tables)

    def get_columns(self, table: str) -> list[dict[str, Any]]:
        return list(self._tables[table]["columns"])

    def get_pk_constraint(self, table: str) -> dict[str, Any]:
        return {"constrained_columns": self._tables[table].get("pk", ["id"])}

    def get_unique_constraints(self, table: str) -> list[dict[str, Any]]:
        return list(self._tables[table].get("unique", []))

    def get_foreign_keys(self, table: str) -> list[dict[str, Any]]:
        return list(self._tables[table].get("fks", []))

    def get_indexes(self, table: str) -> list[dict[str, Any]]:
        return list(self._tables[table].get("indexes", []))


def _col(
    name: str, type_: TypeEngine[Any], nullable: bool, default: str | None = None
) -> dict[str, Any]:
    return {"name": name, "type": type_, "nullable": nullable, "default": default}


# ---------------------------------------------------------------------------
# normalize_type
# ---------------------------------------------------------------------------


class TestNormalizeType:
    def test_includes_string_length(self) -> None:
        assert normalize_type(VARCHAR()) == "VARCHAR"
        assert normalize_type(VARCHAR(4)) == "VARCHAR(4)"

    def test_distinguishes_timezone(self) -> None:
        assert normalize_type(TIMESTAMP(timezone=True)) == "TIMESTAMP WITH TIME ZONE"
        assert normalize_type(TIMESTAMP()) == "TIMESTAMP WITHOUT TIME ZONE"


# ---------------------------------------------------------------------------
# snapshot_from_inspector
# ---------------------------------------------------------------------------


class TestSnapshotFromInspector:
    def test_skips_alembic_version_table(self) -> None:
        inspector = FakeInspector(
            {
                "alembic_version": {"columns": [_col("version_num", VARCHAR(32), False)]},
                "services": {"columns": [_col("id", Integer(), False)]},
            }
        )
        snapshot = snapshot_from_inspector(inspector)
        assert set(snapshot.tables) == {"services"}

    def test_captures_column_attributes_with_normalized_types(self) -> None:
        inspector = FakeInspector(
            {
                "metrics": {
                    "columns": [
                        _col("id", Integer(), False),
                        _col("checked_at", TIMESTAMP(timezone=True), False, "now()"),
                    ]
                }
            }
        )
        table = snapshot_from_inspector(inspector).tables["metrics"]
        assert ColumnInfo("checked_at", "TIMESTAMP WITH TIME ZONE", False, "now()") in table.columns

    def test_constraints_and_indexes_are_name_insensitive(self) -> None:
        def services(uq_name: str) -> dict[str, Any]:
            return {
                "columns": [_col("id", Integer(), False), _col("url", VARCHAR(), False)],
                "unique": [{"name": uq_name, "column_names": ["url"]}],
                "indexes": [
                    {"name": "ix_services_id", "column_names": ["id"], "unique": False},
                    # Backing index of the unique constraint: must be ignored.
                    {
                        "name": uq_name,
                        "column_names": ["url"],
                        "unique": True,
                        "duplicates_constraint": uq_name,
                    },
                ],
            }

        migrated = snapshot_from_inspector(FakeInspector({"services": services("uq_services_url")}))
        models = snapshot_from_inspector(FakeInspector({"services": services("services_url_key")}))
        assert migrated == models
        assert migrated.tables["services"].unique_constraints == frozenset({("url",)})
        assert migrated.tables["services"].indexes == frozenset({(("id",), False)})

    def test_captures_foreign_key_delete_rule(self) -> None:
        inspector = FakeInspector(
            {
                "metrics": {
                    "columns": [_col("id", Integer(), False), _col("service_id", Integer(), False)],
                    "fks": [
                        {
                            "name": "metrics_service_id_fkey",
                            "constrained_columns": ["service_id"],
                            "referred_table": "services",
                            "referred_columns": ["id"],
                            "options": {"ondelete": "CASCADE"},
                        }
                    ],
                }
            }
        )
        fks = snapshot_from_inspector(inspector).tables["metrics"].foreign_keys
        assert fks == frozenset({ForeignKeyInfo(("service_id",), "services", ("id",), "CASCADE")})


# ---------------------------------------------------------------------------
# diff_schemas
# ---------------------------------------------------------------------------


class TestDiffSchemas:
    def test_identical_schemas_have_no_differences(self) -> None:
        snap = SchemaSnapshot({"metrics": _table(BASE_COLUMNS)})
        assert diff_schemas(snap, snap) == []

    def test_reports_string_length_difference(self) -> None:
        migrated = SchemaSnapshot({"metrics": _table([("status", "VARCHAR", False, None)])})
        models = SchemaSnapshot({"metrics": _table([("status", "VARCHAR(4)", False, None)])})
        assert diff_schemas(migrated, models) == [
            "metrics.status: type VARCHAR (migrated) != VARCHAR(4) (models)"
        ]

    def test_reports_timezone_difference(self) -> None:
        migrated = SchemaSnapshot(
            {"metrics": _table([("checked_at", "TIMESTAMP WITHOUT TIME ZONE", False, "now()")])}
        )
        models = SchemaSnapshot(
            {"metrics": _table([("checked_at", "TIMESTAMP WITH TIME ZONE", False, "now()")])}
        )
        assert diff_schemas(migrated, models) == [
            "metrics.checked_at: type TIMESTAMP WITHOUT TIME ZONE (migrated) "
            "!= TIMESTAMP WITH TIME ZONE (models)"
        ]

    def test_reports_nullability_and_default_differences(self) -> None:
        migrated = SchemaSnapshot(
            {"metrics": _table([("checked_at", "TIMESTAMP WITH TIME ZONE", True, None)])}
        )
        models = SchemaSnapshot(
            {"metrics": _table([("checked_at", "TIMESTAMP WITH TIME ZONE", False, "now()")])}
        )
        assert diff_schemas(migrated, models) == [
            "metrics.checked_at: default None (migrated) != 'now()' (models)",
            "metrics.checked_at: nullable True (migrated) != False (models)",
        ]

    def test_reports_missing_and_extra_tables(self) -> None:
        migrated = SchemaSnapshot({"legacy": _table(BASE_COLUMNS)})
        models = SchemaSnapshot({"metrics": _table(BASE_COLUMNS)})
        assert diff_schemas(migrated, models) == [
            "table legacy: in migrated schema but not in models",
            "table metrics: in models but not in migrated schema",
        ]

    def test_reports_missing_and_extra_columns(self) -> None:
        migrated = SchemaSnapshot(
            {"metrics": _table(BASE_COLUMNS + [("old_col", "INTEGER", True, None)])}
        )
        models = SchemaSnapshot(
            {"metrics": _table(BASE_COLUMNS + [("new_col", "INTEGER", True, None)])}
        )
        assert diff_schemas(migrated, models) == [
            "metrics.new_col: column in models but not in migrated schema",
            "metrics.old_col: column in migrated schema but not in models",
        ]

    def test_reports_foreign_key_delete_rule_difference(self) -> None:
        def with_fk(ondelete: str | None) -> SchemaSnapshot:
            fk = ForeignKeyInfo(("service_id",), "services", ("id",), ondelete)
            return SchemaSnapshot({"metrics": _table(BASE_COLUMNS, foreign_keys=frozenset({fk}))})

        diffs = diff_schemas(with_fk(None), with_fk("CASCADE"))
        assert diffs == [
            "metrics: foreign key (service_id) -> services(id) ON DELETE CASCADE "
            "in models but not in migrated schema",
            "metrics: foreign key (service_id) -> services(id) ON DELETE NO ACTION "
            "in migrated schema but not in models",
        ]

    def test_reports_index_uniqueness_difference(self) -> None:
        migrated = SchemaSnapshot(
            {"metrics": _table(BASE_COLUMNS, indexes=frozenset({(("checked_at",), False)}))}
        )
        models = SchemaSnapshot(
            {"metrics": _table(BASE_COLUMNS, indexes=frozenset({(("checked_at",), True)}))}
        )
        assert diff_schemas(migrated, models) == [
            "metrics: index (checked_at) in migrated schema but not in models",
            "metrics: unique index (checked_at) in models but not in migrated schema",
        ]

    def test_reports_primary_key_and_unique_constraint_differences(self) -> None:
        migrated = SchemaSnapshot({"services": _table(BASE_COLUMNS, primary_key=("id",))})
        models = SchemaSnapshot(
            {
                "services": _table(
                    BASE_COLUMNS, primary_key=("id", "status"), unique=frozenset({("status",)})
                )
            }
        )
        assert diff_schemas(migrated, models) == [
            "services: primary key (id) (migrated) != (id, status) (models)",
            "services: unique constraint (status) in models but not in migrated schema",
        ]

    def test_output_is_deterministic_and_sorted(self) -> None:
        migrated = SchemaSnapshot(
            {
                "services": _table([("is_active", "BOOLEAN", True, None)]),
                "metrics": _table([("status", "VARCHAR", True, None)]),
            }
        )
        models = SchemaSnapshot(
            {
                "services": _table([("is_active", "BOOLEAN", False, None)]),
                "metrics": _table([("status", "VARCHAR(4)", False, None)]),
            }
        )
        diffs = diff_schemas(migrated, models)
        assert diffs == sorted(diffs)
        assert diffs == diff_schemas(migrated, models)
        assert len(diffs) == 3


# ---------------------------------------------------------------------------
# format_differences and the CLI entry point
# ---------------------------------------------------------------------------


class TestFormatDifferences:
    def test_no_differences(self) -> None:
        assert (
            format_differences([])
            == "No schema differences between the migration history and the models."
        )

    def test_lists_each_difference_with_count(self) -> None:
        text = format_differences(["a: x", "b: y"])
        assert text.splitlines() == [
            "2 schema difference(s) between the migration history and the models:",
            "  - a: x",
            "  - b: y",
        ]


def test_boolean_type_normalizes() -> None:
    assert normalize_type(Boolean()) == "BOOLEAN"


# ---------------------------------------------------------------------------
# CLI entry point (setup errors only; the PostgreSQL path is covered in tests/postgres)
# ---------------------------------------------------------------------------


class TestMain:
    def test_exits_2_with_instructions_when_url_missing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("TEST_POSTGRES_URL", raising=False)
        assert main() == 2
        err = capsys.readouterr().err
        assert "TEST_POSTGRES_URL" in err
        assert "docker compose up -d db" in err

    def test_exits_2_when_server_unreachable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("TEST_POSTGRES_URL", "postgresql://u:p@127.0.0.1:1/postgres")
        assert main() == 2
        assert "Cannot connect" in capsys.readouterr().err
