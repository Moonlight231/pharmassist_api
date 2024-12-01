"""add low stock date to branch products and update analytics timeseries

Revision ID: adbd5fa2776f
Revises: resync_branch_quantities
Create Date: 2024-12-01 07:26:29.165926

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'adbd5fa2776f'
down_revision: Union[str, None] = 'resync_branch_quantities'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # First drop the analytics_timeseries table if it exists
    op.drop_table('analytics_timeseries', if_exists=True)
    
    # Create the analytics_timeseries table with the new schema
    op.create_table(
        'analytics_timeseries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('metric_name', sa.String(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('branch_id', sa.Integer(), nullable=True),
        sa.Column('product_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['product_id'], ['products.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_analytics_timeseries_id', 'analytics_timeseries', ['id'], unique=False)
    
    # Add low_stock_since column to branch_products
    op.add_column('branch_products', sa.Column('low_stock_since', sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Remove low_stock_since from branch_products
    op.drop_column('branch_products', 'low_stock_since')
    
    # Drop the new analytics_timeseries table
    op.drop_table('analytics_timeseries')
    
    # Recreate the old analytics_timeseries table
    op.create_table(
        'analytics_timeseries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('metric_type', sa.VARCHAR(length=50), nullable=False),
        sa.Column('metric_value', sa.DOUBLE_PRECISION(precision=53), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('timestamp', postgresql.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
