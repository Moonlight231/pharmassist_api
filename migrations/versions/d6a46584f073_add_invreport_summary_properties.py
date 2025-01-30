"""add_invreport_summary_properties

Revision ID: d6a46584f073
Revises: aac857bca7a6
Create Date: 2025-01-30 12:22:10.800533

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6a46584f073'
down_revision: Union[str, None] = 'aac857bca7a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
