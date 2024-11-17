"""set default values for wholesale support

Revision ID: set_wholesale_defaults
Revises: be7843c76fa2
Create Date: 2024-11-16 xx:xx:xx.xxxxxx
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
from api.models import Product, Branch

revision: str = 'set_wholesale_defaults'
down_revision: Union[str, None] = 'be7843c76fa2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    
    # Copy low_stock_threshold value to retail_low_stock_threshold
    op.execute("""
        UPDATE products 
        SET retail_low_stock_threshold = 50,
            wholesale_low_stock_threshold = 50,
            is_retail_available = true,
            is_wholesale_available = false
        WHERE retail_low_stock_threshold IS NULL
    """)
    
    # Set default branch type for existing branches
    op.execute("""
        UPDATE branches 
        SET branch_type = 'retail'
        WHERE branch_type IS NULL
    """)

    # After existing upgrade code
    op.execute("""
        UPDATE branch_products bp
        SET is_available = false
        FROM branches b, products p
        WHERE bp.branch_id = b.id 
        AND bp.product_id = p.id
        AND (
            (b.branch_type = 'wholesale' AND NOT p.is_wholesale_available)
            OR (b.branch_type = 'retail' AND NOT p.is_retail_available)
        )
    """)
    
    session.commit()

def downgrade() -> None:
    pass 