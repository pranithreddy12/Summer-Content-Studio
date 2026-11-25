"""0005_app_settings

Studio Settings page: non-secret options saved in the database (override .env).

Revision ID: b3c5d7e9f1a2
Revises: 5096f3308c1a
Create Date: 2026-11-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db import UTCDateTime


# revision identifiers, used by Alembic.
revision: str = 'b3c5d7e9f1a2'
down_revision: Union[str, Sequence[str], None] = '5096f3308c1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('updated_at', UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('app_settings')
