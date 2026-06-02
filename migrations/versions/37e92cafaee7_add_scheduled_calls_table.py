"""add scheduled_calls table

Revision ID: 37e92cafaee7
Revises: cb1a8f32ab59
Create Date: 2026-04-05 20:32:23.701725

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '37e92cafaee7'
down_revision: Union[str, None] = 'cb1a8f32ab59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Table may already exist if created by SQLModel.metadata.create_all
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'scheduledcall')"
    ))
    exists = result.scalar()
    if exists:
        return

    op.create_table('scheduledcall',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('scheduler_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('participant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(), nullable=False),
        sa.Column('is_video_call', sa.Boolean(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('call_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('reminder_sent_at', sa.DateTime(), nullable=True),
        sa.Column('trigger_sent_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['call_id'], ['call.id'], ),
        sa.ForeignKeyConstraint(['cancelled_by'], ['user.id'], ),
        sa.ForeignKeyConstraint(['participant_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['scheduler_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_scheduledcall_participant_id'), 'scheduledcall', ['participant_id'], unique=False)
    op.create_index(op.f('ix_scheduledcall_scheduled_at'), 'scheduledcall', ['scheduled_at'], unique=False)
    op.create_index(op.f('ix_scheduledcall_scheduler_id'), 'scheduledcall', ['scheduler_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_scheduledcall_scheduler_id'), table_name='scheduledcall')
    op.drop_index(op.f('ix_scheduledcall_scheduled_at'), table_name='scheduledcall')
    op.drop_index(op.f('ix_scheduledcall_participant_id'), table_name='scheduledcall')
    op.drop_table('scheduledcall')
