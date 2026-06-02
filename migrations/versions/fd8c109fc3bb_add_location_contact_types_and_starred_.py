"""add location contact types and starred messages

Revision ID: fd8c109fc3bb
Revises: af76f7f95968
Create Date: 2026-03-29 02:06:25.677331

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'fd8c109fc3bb'
down_revision: Union[str, None] = 'af76f7f95968'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # Create starred messages table (skip if already exists)
    if 'starredmessage' not in existing_tables:
        op.create_table('starredmessage',
            sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column('message_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['message_id'], ['message.id'], ),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('starredmessage', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_starredmessage_message_id'), ['message_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_starredmessage_user_id'), ['user_id'], unique=False)

    # Add new columns to message table (idempotent — skip if already present)
    existing_cols = {c['name'] for c in inspector.get_columns('message')}
    with op.batch_alter_table('message', schema=None) as batch_op:
        if 'latitude' not in existing_cols:
            batch_op.add_column(sa.Column('latitude', sa.Float(), nullable=True))
        if 'longitude' not in existing_cols:
            batch_op.add_column(sa.Column('longitude', sa.Float(), nullable=True))
        if 'location_name' not in existing_cols:
            batch_op.add_column(sa.Column('location_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        if 'contact_name' not in existing_cols:
            batch_op.add_column(sa.Column('contact_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        if 'contact_phone' not in existing_cols:
            batch_op.add_column(sa.Column('contact_phone', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        if 'contact_user_id' not in existing_cols:
            batch_op.add_column(sa.Column('contact_user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    # For PostgreSQL: add the new enum values to the existing native enum type.
    # For SQLite: no native enum type exists, so nothing to alter.
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'LOCATION'")
        op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'CONTACT'")


def downgrade() -> None:
    with op.batch_alter_table('message', schema=None) as batch_op:
        batch_op.drop_column('contact_user_id')
        batch_op.drop_column('contact_phone')
        batch_op.drop_column('contact_name')
        batch_op.drop_column('location_name')
        batch_op.drop_column('longitude')
        batch_op.drop_column('latitude')

    with op.batch_alter_table('starredmessage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_starredmessage_user_id'))
        batch_op.drop_index(batch_op.f('ix_starredmessage_message_id'))

    op.drop_table('starredmessage')
