"""sync branch products with batches

Revision ID: sync_batches
Revises: cef0d13876ce
Create Date: 2024-11-07 10:30:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from datetime import date, timedelta
from sqlalchemy.orm import Session
from api.models import BranchProduct, ProductBatch

# revision identifiers, used by Alembic
revision: str = 'sync_batches'  # Shortened revision ID
down_revision: Union[str, None] = 'cef0d13876ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    
    # Get all branch products
    branch_products = session.query(BranchProduct).all()
    
    # Set expiration date to 1 year from now for legacy data
    default_expiry = date.today() + timedelta(days=365)
    
    for bp in branch_products:
        # Get total from active batches
        total_batch_quantity = session.query(sa.func.sum(ProductBatch.quantity))\
            .filter(
                ProductBatch.branch_id == bp.branch_id,
                ProductBatch.product_id == bp.product_id,
                ProductBatch.is_active == True
            ).scalar() or 0
        
        # If no batches exist but branch_product has quantity, create a default batch
        if total_batch_quantity == 0 and bp.quantity > 0:
            print(f"Creating legacy batch for branch_id={bp.branch_id}, product_id={bp.product_id}, quantity={bp.quantity}")
            new_batch = ProductBatch(
                branch_id=bp.branch_id,
                product_id=bp.product_id,
                lot_number='LEGACY_DATA',
                quantity=bp.quantity,
                expiration_date=default_expiry,
                is_active=True,
                created_at=date.today()  # Set creation date to today
            )
            session.add(new_batch)
        elif total_batch_quantity != bp.quantity:
            print(f"Mismatch found for branch_id={bp.branch_id}, product_id={bp.product_id}")
            print(f"Branch product quantity: {bp.quantity}, Batch total: {total_batch_quantity}")
            # Update branch_product quantity to match batches if batches exist
            if total_batch_quantity > 0:
                print(f"Updating branch product quantity to {total_batch_quantity}")
                bp.quantity = total_batch_quantity
    
    session.commit()

def downgrade() -> None:
    # We don't want to remove the legacy batches in downgrade
    pass 