from datetime import timedelta, datetime, timezone
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt
from dotenv import load_dotenv
import os
from api.models import User, UserRole, Branch
from api.deps import db_dependency, bcrypt_context

load_dotenv()

router = APIRouter(
    prefix='/auth',
    tags=['auth'],
)

SECRET_KEY = os.getenv('AUTH_SECRET_KEY')
ALGORITHM = os.getenv('AUTH_ALGORITHM')


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: UserRole
    branch_id: Optional[int] = None

class Token(BaseModel):
    access_token: str
    token_type: str

def authenticate_user(username: str, password: str, db):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return False
    if not bcrypt_context.verify(password, user.hashed_password):
        return False
    return user

def create_access_token(username: str, user_id: int, role: UserRole, branch_id: Optional[int], expires_delta: timedelta):
    encode = {
        'sub': username, 
        'id': user_id, 
        'role': role.value,
        'branch_id': branch_id
    }
    expires = datetime.now(timezone.utc) + expires_delta
    encode.update({'exp': expires})
    return jwt.encode(encode, SECRET_KEY, algorithm=ALGORITHM)

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(db: db_dependency, create_user_request: UserCreateRequest):
    # Check if username already exists
    existing_user = db.query(User).filter(User.username == create_user_request.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )

    # Validate branch_id requirement for branch-specific roles
    if create_user_request.role in [UserRole.PHARMACIST, UserRole.WHOLESALER]:
        if not create_user_request.branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Branch ID is required for {create_user_request.role.value} users"
            )
        # Verify branch exists and matches role type
        branch = db.query(Branch).filter(Branch.id == create_user_request.branch_id).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found"
            )
        if create_user_request.role == UserRole.WHOLESALER and branch.branch_type != 'wholesale':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Wholesaler users can only be assigned to wholesale branches"
            )
        if create_user_request.role == UserRole.PHARMACIST and branch.branch_type != 'retail':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pharmacist users can only be assigned to retail branches"
            )

    # For other roles, ensure branch_id is None
    if create_user_request.role not in [UserRole.PHARMACIST, UserRole.WHOLESALER]:
        create_user_request.branch_id = None

    create_user_model = User(
        username=create_user_request.username,
        hashed_password=bcrypt_context.hash(create_user_request.password),
        role=create_user_request.role.value,
        branch_id=create_user_request.branch_id
    )
    
    db.add(create_user_model)
    db.commit()
    return {"message": "User created successfully"}

@router.post('/token', response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: db_dependency
):
    user = authenticate_user(form_data.username, form_data.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate user"
        )
    token = create_access_token(
        user.username,
        user.id,
        UserRole(user.role),
        user.branch_id,
        timedelta(minutes=20)
    )
    return {'access_token': token, 'token_type': 'bearer'}