"""add ar filters and game configs

Revision ID: e3f2a1b9c7d8
Revises: ca7bc130e901
Create Date: 2026-05-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e3f2a1b9c7d8'
down_revision: Union[str, None] = 'ca7bc130e901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'arfilter',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('filter_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('filter_data', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_arfilter_filter_key'), 'arfilter', ['filter_key'], unique=True)

    op.create_table(
        'argameconfig',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('game_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('config_data', sa.JSON(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_argameconfig_game_id'), 'argameconfig', ['game_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_argameconfig_game_id'), table_name='argameconfig')
    op.drop_table('argameconfig')
    op.drop_index(op.f('ix_arfilter_filter_key'), table_name='arfilter')
    op.drop_table('arfilter')
