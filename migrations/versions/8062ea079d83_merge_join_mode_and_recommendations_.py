"""merge join_mode and recommendations heads

Revision ID: 8062ea079d83
Revises: a1b2c3d4e5f6, a862d666f07f
Create Date: 2026-05-24 17:23:52.788057

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '8062ea079d83'
down_revision: Union[str, None] = ('a1b2c3d4e5f6', 'a862d666f07f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
