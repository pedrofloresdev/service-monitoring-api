"""add created_at to services, float response_time_ms, tz-aware checked_at

Revision ID: 3f8a12b9c4d1
Revises: aa93def1070e
Create Date: 2026-05-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3f8a12b9c4d1'
down_revision: Union[str, Sequence[str], None] = 'aa93def1070e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add created_at to services with server default
    op.add_column(
        'services',
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
    )

    # Add unique constraint on url
    op.create_unique_constraint('uq_services_url', 'services', ['url'])

    # Change response_time_ms from Integer to Float for sub-ms precision
    op.alter_column(
        'metrics',
        'response_time_ms',
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=True,
    )

    # Change checked_at to timezone-aware DateTime
    op.alter_column(
        'metrics',
        'checked_at',
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
    )

    # Add index on metrics.checked_at for time-range queries
    op.create_index('ix_metrics_checked_at', 'metrics', ['checked_at'])

    # Add index on metrics.service_id (if not already present)
    op.create_index('ix_metrics_service_id', 'metrics', ['service_id'])

    # Make service_id non-nullable
    op.alter_column('metrics', 'service_id', existing_type=sa.Integer(), nullable=False)

    # Add ON DELETE CASCADE to metrics.service_id foreign key
    op.drop_constraint('metrics_service_id_fkey', 'metrics', type_='foreignkey')
    op.create_foreign_key(
        'metrics_service_id_fkey',
        'metrics', 'services',
        ['service_id'], ['id'],
        ondelete='CASCADE'
    )


def downgrade() -> None:
    op.drop_constraint('metrics_service_id_fkey', 'metrics', type_='foreignkey')
    op.create_foreign_key(
        'metrics_service_id_fkey',
        'metrics', 'services',
        ['service_id'], ['id'],
    )
    op.alter_column('metrics', 'service_id', existing_type=sa.Integer(), nullable=True)
    op.drop_index('ix_metrics_service_id', table_name='metrics')
    op.drop_index('ix_metrics_checked_at', table_name='metrics')
    op.alter_column(
        'metrics',
        'checked_at',
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    op.alter_column(
        'metrics',
        'response_time_ms',
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.drop_constraint('uq_services_url', 'services', type_='unique')
    op.drop_column('services', 'created_at')
