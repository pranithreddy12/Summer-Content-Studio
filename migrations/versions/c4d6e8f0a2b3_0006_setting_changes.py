"""0006_setting_changes

Settings page change history.

Revision ID: c4d6e8f0a2b3
Revises: b3c5d7e9f1a2
Create Date: 2026-11-26 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db import UTCDateTime


# revision identifiers, used by Alembic.
revision: str = 'c4d6e8f0a2b3'
down_revision: Union[str, Sequence[str], None] = 'b3c5d7e9f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('setting_changes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('old', sa.String(length=200), nullable=False),
    sa.Column('new', sa.String(length=200), nullable=False),
    sa.Column('at', UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_setting_changes_at'), 'setting_changes', ['at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_setting_changes_at'), table_name='setting_changes')
    op.drop_table('setting_changes')
