"""add join_mode to chat

Revision ID: a1b2c3d4e5f6
Revises: e3f2a1b9c7d8
Create Date: 2026-05-24

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'e3f2a1b9c7d8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('chat', schema=None) as batch_op:
        batch_op.add_column(sa.Column('join_mode', sa.String(), nullable=False, server_default='private'))


def downgrade():
    with op.batch_alter_table('chat', schema=None) as batch_op:
        batch_op.drop_column('join_mode')
