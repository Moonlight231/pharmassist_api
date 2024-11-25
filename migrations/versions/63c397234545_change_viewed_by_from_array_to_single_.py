"""change viewed_by from array to single integer

Revision ID: 63c397234545
Revises: 41953fddeaad
Create Date: 2024-11-25 10:19:51.141959

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '63c397234545'
down_revision: Union[str, None] = '41953fddeaad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the ARRAY column
    op.drop_column('invreports', 'viewed_by')
    # Add new integer column
    op.add_column('invreports', sa.Column('viewed_by', sa.Integer(), nullable=True))

def downgrade() -> None:
    # Drop the integer column
    op.drop_column('invreports', 'viewed_by')
    # Add back ARRAY column
    op.add_column('invreports', sa.Column('viewed_by', sa.ARRAY(sa.Integer()), nullable=True))