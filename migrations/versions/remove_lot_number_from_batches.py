"""remove lot number from batches

Revision ID: remove_lot_number_from_batches
Revises: sync_branch_product_quantities
Create Date: 2024-11-08 10:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
from api.models import ProductBatch, InvReportBatch

revision: str = 'remove_lot_number_from_batches'
down_revision: Union[str, None] = 'sync_branch_product_quantities'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    
    # First merge batches with same expiration date
    batches = session.query(ProductBatch)\
        .filter(ProductBatch.is_active == True)\
        .all()
    
    # Group batches by branch, product, and expiration date
    batch_groups = {}
    for batch in batches:
        key = (batch.branch_id, batch.product_id, batch.expiration_date)
        if key not in batch_groups:
            batch_groups[key] = []
        batch_groups[key].append(batch)
    
    # Merge batches with same expiration date
    for (branch_id, product_id, expiration_date), group in batch_groups.items():
        if len(group) > 1:
            print(f"Merging {len(group)} batches for branch={branch_id}, product={product_id}, expiry={expiration_date}")
            total_quantity = sum(batch.quantity for batch in group)
            # Keep first batch and update quantity
            primary_batch = group[0]
            primary_batch.quantity = total_quantity
            # Delete other batches
            for batch in group[1:]:
                session.delete(batch)
    
    session.commit()
    
    # Remove lot_number columns
    op.drop_column('product_batches', 'lot_number')
    op.drop_column('invreport_batches', 'lot_number')

def downgrade() -> None:
    op.add_column('product_batches', sa.Column('lot_number', sa.String(), nullable=True))
    op.add_column('invreport_batches', sa.Column('lot_number', sa.String(), nullable=True)) 