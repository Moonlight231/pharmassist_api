"""sync branch product quantities

Revision ID: sync_branch_product_quantities
Revises: cbbcb3661849
Create Date: 2024-11-08 10:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
from api.models import BranchProduct, ProductBatch

revision: str = 'sync_branch_product_quantities'
down_revision: Union[str, None] = 'cbbcb3661849'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    
    # Get all branch products
    branch_products = session.query(BranchProduct).all()
    
    for bp in branch_products:
        # Calculate total from active batches
        total_quantity = session.query(sa.func.sum(ProductBatch.quantity))\
            .filter(
                ProductBatch.branch_id == bp.branch_id,
                ProductBatch.product_id == bp.product_id,
                ProductBatch.is_active == True
            ).scalar() or 0
        
        # Update branch product quantity if different
        if bp.quantity != total_quantity:
            print(f"Updating branch_id={bp.branch_id}, product_id={bp.product_id}")
            print(f"Old quantity: {bp.quantity}, New quantity: {total_quantity}")
            bp.quantity = total_quantity
    
    session.commit()

def downgrade() -> None:
    pass