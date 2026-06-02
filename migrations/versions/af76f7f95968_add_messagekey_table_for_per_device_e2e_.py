"""add messagekey table for per-device e2e keys

Revision ID: af76f7f95968
Revises: 99d941697771
Create Date: 2026-03-26 03:38:55.937116

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'af76f7f95968'
down_revision: Union[str, None] = '99d941697771'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'messagekey',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('message_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('device_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('encrypted_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('key_slot', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['message.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_messagekey_message_id'), 'messagekey', ['message_id'], unique=False)
    op.create_index(op.f('ix_messagekey_device_id'), 'messagekey', ['device_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_messagekey_device_id'), table_name='messagekey')
    op.drop_index(op.f('ix_messagekey_message_id'), table_name='messagekey')
    op.drop_table('messagekey')
