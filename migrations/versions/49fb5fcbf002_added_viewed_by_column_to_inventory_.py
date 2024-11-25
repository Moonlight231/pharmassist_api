"""added viewed_by column to inventory reports

Revision ID: 49fb5fcbf002
Revises: f22d82a3a71d
Create Date: 2024-11-24 10:01:08.753904

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '49fb5fcbf002'
down_revision: Union[str, None] = 'f22d82a3a71d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
