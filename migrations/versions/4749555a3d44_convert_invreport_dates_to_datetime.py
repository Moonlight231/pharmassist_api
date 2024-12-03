"""convert_invreport_dates_to_datetime

Revision ID: 4749555a3d44
Revises: adbd5fa2776f
Create Date: 2024-12-03 15:16:33.238802

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4749555a3d44'
down_revision: Union[str, None] = 'adbd5fa2776f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Convert start_date and end_date columns from Date to DateTime
    op.execute("""
        ALTER TABLE invreports 
        ALTER COLUMN start_date TYPE TIMESTAMP USING start_date::timestamp,
        ALTER COLUMN end_date TYPE TIMESTAMP USING end_date::timestamp
    """)

def downgrade() -> None:
    # Convert back to Date if needed
    op.execute("""
        ALTER TABLE invreports 
        ALTER COLUMN start_date TYPE DATE USING start_date::date,
        ALTER COLUMN end_date TYPE DATE USING end_date::date
    """)
