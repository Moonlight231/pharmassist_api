"""add low stock threshold

Revision ID: 37b1a7503714
Revises: 78733c3f248a
Create Date: 2024-11-10 20:34:20.532584

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '37b1a7503714'
down_revision: Union[str, None] = '78733c3f248a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
