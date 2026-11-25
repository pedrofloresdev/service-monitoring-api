"""add service origin

Spec 003a RNF-2: services.origin records whether a service is seeded, a visitor service or an
ordinary one. Existing rows become "ordinary" through the server default. Approved in the
003a plan (2026-11-24).

Revision ID: 30fbc6a1211a
Revises: a62134b6f191
Create Date: 2026-11-24 17:22:14.034241

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "30fbc6a1211a"
down_revision: Union[str, Sequence[str], None] = "a62134b6f191"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "services",
        sa.Column(
            "origin",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'ordinary'"),
        ),
    )
    op.create_check_constraint(
        "ck_services_origin", "services", "origin IN ('seeded', 'visitor', 'ordinary')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_services_origin", "services", type_="check")
    op.drop_column("services", "origin")
