"""added profile image_url

Revision ID: 874af8a2331f
Revises: fbc4f2b00708
Create Date: 2024-12-19 16:50:13.443832

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '874af8a2331f'
down_revision: Union[str, None] = 'fbc4f2b00708'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('profiles', sa.Column('image_url', sa.String(), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('profiles', 'image_url')
    # ### end Alembic commands ###