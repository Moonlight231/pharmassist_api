"""add availaility status to branch products and exempts to low stock status if unavailable

Revision ID: 37136e077035
Revises: 092883a7a98d
Create Date: 2024-11-11 13:34:31.688983

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '37136e077035'
down_revision: Union[str, None] = '092883a7a98d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('branch_products', sa.Column('is_available', sa.Boolean(), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('branch_products', 'is_available')
    # ### end Alembic commands ###
