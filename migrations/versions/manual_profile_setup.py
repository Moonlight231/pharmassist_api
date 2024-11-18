"""setup profiles for existing users

Revision ID: manual_profile_setup
Revises: 010e09329088
Create Date: 2024-11-18 12:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
from api.models import User, Profile

revision: str = 'manual_profile_setup'
down_revision: Union[str, None] = '010e09329088'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    
    # Set has_changed_password to True for existing users
    op.execute("""
        UPDATE users 
        SET has_changed_password = true,
            initial_password = NULL
        WHERE has_changed_password IS NULL
    """)
    
    # Create default profiles for users who don't have one
    users = session.query(User).all()
    for user in users:
        existing_profile = session.query(Profile).filter(Profile.user_id == user.id).first()
        if not existing_profile:
            default_profile = Profile(
                user_id=user.id,
                first_name=f"User {user.id}",
                last_name=user.username,
                email=f"{user.username}@example.com"
            )
            session.add(default_profile)
    
    session.commit()

def downgrade() -> None:
    pass 