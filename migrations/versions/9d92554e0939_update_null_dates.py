"""update null dates

Revision ID: 9d92554e0939
Revises: update_null_dates
Create Date: 2024-11-06 02:57:36.839141

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d92554e0939'
down_revision: Union[str, None] = 'update_null_dates'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
