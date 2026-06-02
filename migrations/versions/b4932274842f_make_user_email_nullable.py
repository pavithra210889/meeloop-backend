"""make user email nullable

Revision ID: b4932274842f
Revises: 8062ea079d83
Create Date: 2026-05-24 17:28:02.108657

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4932274842f'
down_revision: Union[str, None] = '8062ea079d83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make column nullable first, then clear placeholder emails
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('email',
               existing_type=sa.VARCHAR(),
               nullable=True)
    op.execute(
        "UPDATE \"user\" SET email = NULL "
        "WHERE email LIKE '%@phone.placeholder' OR email LIKE '%@truecaller.temp'"
    )


def downgrade() -> None:
    # Restore NOT NULL — fill any NULLs with a placeholder first to avoid constraint errors
    op.execute(
        "UPDATE \"user\" SET email = id || '@removed.placeholder' WHERE email IS NULL"
    )
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('email',
               existing_type=sa.VARCHAR(),
               nullable=False)
