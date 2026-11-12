"""align schema with models

Spec 001-migration-drift. The earlier revisions never gave metrics.checked_at a server
default, so PostgreSQL stored NULL for every check. This revision aligns the migrated schema
with the models:

- metrics.checked_at: DEFAULT now(), NOT NULL. Legacy rows with NULL checked_at are DELETED
  and the count is logged (owner-approved one-time exception to constitution principle 1).
- metrics.status: VARCHAR(4), NOT NULL. Aborts, changing nothing, if any row has a NULL or
  longer status: health data is never invented or truncated.
- services.expected_status / check_interval / is_active: NOT NULL, NULLs set to the model
  defaults (200 / 60 / true) first.

Revision ID: a62134b6f191
Revises: 3f8a12b9c4d1
Create Date: 2026-11-12 14:28:44.490684

"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "a62134b6f191"
down_revision: Union[str, Sequence[str], None] = "3f8a12b9c4d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

SERVICE_DEFAULTS = (
    ("expected_status", sa.Integer(), "200"),
    ("check_interval", sa.Integer(), "60"),
    ("is_active", sa.Boolean(), "true"),
)


def upgrade() -> None:
    """Upgrade schema."""
    if context.is_offline_mode():
        log.info("Offline mode: legacy metric count unavailable; status precheck skipped")
        op.execute("DELETE FROM metrics WHERE checked_at IS NULL")
    else:
        bind = op.get_bind()
        bad = bind.execute(
            sa.text("SELECT count(*) FROM metrics WHERE status IS NULL OR length(status) > 4")
        ).scalar_one()
        if bad:
            # Raising inside the revision's transaction rolls back everything.
            raise RuntimeError(
                f"{bad} metrics row(s) have a NULL or >4-character status; "
                "fix them and re-run. No changes were made."
            )
        deleted = bind.execute(sa.text("DELETE FROM metrics WHERE checked_at IS NULL")).rowcount
        log.info("Deleted %d legacy metric(s) with NULL checked_at", deleted)

    for column, _type, default in SERVICE_DEFAULTS:
        op.execute(f"UPDATE services SET {column} = {default} WHERE {column} IS NULL")

    op.alter_column(
        "metrics",
        "checked_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )
    op.alter_column(
        "metrics",
        "status",
        existing_type=sa.String(),
        type_=sa.String(length=4),
        nullable=False,
    )
    for column, type_, _default in SERVICE_DEFAULTS:
        op.alter_column("services", column, existing_type=type_, nullable=False)


def downgrade() -> None:
    """Downgrade schema. Rows are kept; legacy metrics deleted on upgrade are not restored."""
    for column, type_, _default in SERVICE_DEFAULTS:
        op.alter_column("services", column, existing_type=type_, nullable=True)
    op.alter_column(
        "metrics",
        "status",
        existing_type=sa.String(length=4),
        type_=sa.String(),
        nullable=True,
    )
    op.alter_column(
        "metrics",
        "checked_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=None,
        nullable=True,
    )
