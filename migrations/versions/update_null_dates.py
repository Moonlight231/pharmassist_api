"""update null dates in invreports

Revision ID: update_null_dates
Revises: fa123e1ec802
Create Date: 2024-03-21 12:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'update_null_dates'
down_revision: Union[str, None] = 'fa123e1ec802'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Update existing records with NULL dates to use created_at date
    op.execute("""
        UPDATE invreports 
        SET start_date = DATE(created_at),
            end_date = DATE(created_at)
        WHERE start_date IS NULL OR end_date IS NULL
    """)

def downgrade() -> None:
    pass  