"""merge_heads

Revision ID: 86388769acd9
Revises: 37e92cafaee7, e3f2a1b9c7d8
Create Date: 2026-05-10 22:15:57.205468

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '86388769acd9'
down_revision: Union[str, None] = ('37e92cafaee7', 'e3f2a1b9c7d8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
