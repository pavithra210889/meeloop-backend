"""add_postgis_location_to_user_and_loopprofile

Revision ID: 193fcae71068
Revises: ca7bc130e901
Create Date: 2026-03-29 17:57:17.019155

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import geoalchemy2


# revision identifiers, used by Alembic.
revision: str = '193fcae71068'
down_revision: Union[str, None] = 'ca7bc130e901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable PostGIS extension
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")


    with op.batch_alter_table('loopprofile', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location', geoalchemy2.types.Geography(geometry_type='POINT', srid=4326, dimension=2, from_text='ST_GeogFromText', name='geography'), nullable=True))
        batch_op.add_column(sa.Column('location_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('location_updated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('location_sharing_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        batch_op.create_index('idx_loopprofile_location_gist', ['location'], unique=False, postgresql_using='gist')

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location', geoalchemy2.types.Geography(geometry_type='POINT', srid=4326, dimension=2, from_text='ST_GeogFromText', name='geography'), nullable=True))
        batch_op.add_column(sa.Column('location_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('location_updated_at', sa.DateTime(), nullable=True))
        batch_op.create_index('idx_user_location_gist', ['location'], unique=False, postgresql_using='gist')

    # ### end Alembic commands ###


def downgrade() -> None:
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index('idx_user_location_gist', postgresql_using='gist')
        batch_op.drop_column('location_updated_at')
        batch_op.drop_column('location_name')
        batch_op.drop_column('location')

    with op.batch_alter_table('loopprofile', schema=None) as batch_op:
        batch_op.drop_index('idx_loopprofile_location_gist', postgresql_using='gist')
        batch_op.drop_column('location_sharing_enabled')
        batch_op.drop_column('location_updated_at')
        batch_op.drop_column('location_name')
        batch_op.drop_column('location')

    # ### end Alembic commands ###
