"""convert existing dates to timestamps

Revision ID: data_conversion_timestamp
Revises: 078d9625a1d7
Create Date: 2024-03-21 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from datetime import datetime

# revision identifiers, used by Alembic.
revision: str = 'data_conversion_timestamp'
down_revision: Union[str, None] = '078d9625a1d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Convert existing NULL created_at values using the date_created data
    op.execute("""
        UPDATE invreports 
        SET created_at = CURRENT_TIMESTAMP,
            last_edit = CURRENT_TIMESTAMP 
        WHERE created_at IS NULL
    """)

def downgrade() -> None:
    pass 