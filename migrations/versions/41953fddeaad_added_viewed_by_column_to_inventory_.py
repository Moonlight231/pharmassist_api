"""added viewed_by column to inventory reports

Revision ID: 41953fddeaad
Revises: 49fb5fcbf002
Create Date: 2024-11-24 10:18:35.550846

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '41953fddeaad'
down_revision: Union[str, None] = '49fb5fcbf002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_inventory_reports_id', table_name='inventory_reports')
    op.drop_table('inventory_reports')
    op.add_column('invreports', sa.Column('viewed_by', sa.ARRAY(sa.Integer()), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('invreports', 'viewed_by')
    op.create_table('inventory_reports',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('branch_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('start_date', sa.DATE(), autoincrement=False, nullable=True),
    sa.Column('end_date', sa.DATE(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], name='inventory_reports_branch_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='inventory_reports_pkey')
    )
    op.create_index('ix_inventory_reports_id', 'inventory_reports', ['id'], unique=False)
    # ### end Alembic commands ###
