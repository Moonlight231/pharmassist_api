"""dedupe batches and sync selling area

Revision ID: dedupe_batches
Revises: sync_batches
Create Date: 2024-11-07 15:30:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
from api.models import BranchProduct, ProductBatch

revision: str = 'dedupe_batches'
down_revision: Union[str, None] = 'sync_batches'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    
    # Get all branch products
    branch_products = session.query(BranchProduct).all()
    
    for bp in branch_products:
        # Get all active batches for this branch product
        batches = session.query(ProductBatch).filter(
            ProductBatch.branch_id == bp.branch_id,
            ProductBatch.product_id == bp.product_id,
            ProductBatch.is_active == True
        ).order_by(ProductBatch.expiration_date).all()
        
        # Group batches by lot number
        batch_groups = {}
        for batch in batches:
            key = (batch.lot_number, batch.expiration_date)
            if key not in batch_groups:
                batch_groups[key] = []
            batch_groups[key].append(batch)
        
        # Merge duplicates
        total_quantity = 0
        for (lot_number, expiration_date), group in batch_groups.items():
            if len(group) > 1:
                print(f"Merging {len(group)} duplicates for lot {lot_number}")
                # Sum quantities
                merged_quantity = sum(batch.quantity for batch in group)
                # Keep the first batch and update its quantity
                primary_batch = group[0]
                primary_batch.quantity = merged_quantity
                # Delete other duplicates
                for batch in group[1:]:
                    session.delete(batch)
            else:
                primary_batch = group[0]
            
            total_quantity += primary_batch.quantity
        
        # Update branch product quantity to match total batch quantities
        if bp.quantity != total_quantity:
            print(f"Updating branch_id={bp.branch_id}, product_id={bp.product_id}")
            print(f"Old quantity: {bp.quantity}, New quantity: {total_quantity}")
            bp.quantity = total_quantity
    
    session.commit()

def downgrade() -> None:
    # No downgrade possible as this is a data cleanup
    pass 