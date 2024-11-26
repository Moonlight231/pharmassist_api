from datetime import timedelta, datetime, timezone
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt
from dotenv import load_dotenv
import os
from api.models import User, UserRole, Branch, Profile
from api.deps import db_dependency, bcrypt_context, user_dependency, role_required
from sqlalchemy.orm import joinedload

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

class ProfileCreateRequest(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone_number: Optional[str] = None
    license_number: Optional[str] = None

class ProfileUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    license_number: Optional[str] = None

class PasswordUpdateRequest(BaseModel):
    current_password: str
    new_password: str

class UserCreateResponse(BaseModel):
    message: str
    username: str
    password: str

class ProfileResponse(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone_number: Optional[str] = None
    license_number: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(
        from_attributes=True,  # This allows conversion from SQLAlchemy model
        json_schema_extra = {
            "example": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john.doe@example.com",
                "phone_number": "+1234567890",
                "license_number": "LIC123",
                "created_at": "2024-11-18T12:00:00",
                "updated_at": "2024-11-18T12:00:00"
            }
        }
    )

class MessageResponse(BaseModel):
    message: str

class InitialCredentialsUpdateRequest(BaseModel):
    current_password: str
    new_username: str
    new_password: str

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

@router.post("/", status_code=status.HTTP_201_CREATED, response_model=UserCreateResponse)
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

    # Store the original password to return to admin
    original_password = create_user_request.password
    
    create_user_model = User(
        username=create_user_request.username,
        hashed_password=bcrypt_context.hash(create_user_request.password),
        initial_password=create_user_request.password,
        has_changed_password=False,
        role=create_user_request.role.value,
        branch_id=create_user_request.branch_id
    )
    
    db.add(create_user_model)
    db.commit()
    
    return {
        "message": "User created successfully",
        "username": create_user_request.username,
        "password": original_password
    }

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
        timedelta(hours=12)
    )
    return {'access_token': token, 'token_type': 'bearer'}

@router.post("/profile", status_code=status.HTTP_201_CREATED, response_model=MessageResponse)
async def create_profile(
    db: db_dependency,
    profile_data: ProfileCreateRequest,
    current_user: user_dependency
):
    # Check if profile already exists
    existing_profile = db.query(Profile).filter(
        Profile.user_id == current_user['id']
    ).first()
    
    if existing_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile already exists"
        )

    # Create profile
    new_profile = Profile(
        user_id=current_user['id'],
        **profile_data.model_dump()
    )
    
    db.add(new_profile)
    db.commit()
    db.refresh(new_profile)
    
    return {"message": "Profile created successfully"}

@router.get("/has-profile", response_model=bool)
async def check_profile_exists(
    db: db_dependency,
    current_user: user_dependency
):
    profile = db.query(Profile).filter(
        Profile.user_id == current_user['id']
    ).first()
    return bool(profile)

@router.get("/profile", response_model=ProfileResponse)
async def get_profile(
    db: db_dependency,
    current_user: user_dependency
):
    profile = db.query(Profile).filter(
        Profile.user_id == current_user['id']
    ).first()
    
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found"
        )
    
    return profile

@router.put("/profile", status_code=status.HTTP_200_OK, response_model=MessageResponse)
async def update_profile(
    db: db_dependency,
    profile_data: ProfileUpdateRequest,
    current_user: user_dependency
):
    # Get existing profile
    profile = db.query(Profile).filter(
        Profile.user_id == current_user['id']
    ).first()
    
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found"
        )

    # Update profile fields
    for key, value in profile_data.model_dump(exclude_unset=True).items():
        setattr(profile, key, value)
    
    profile.updated_at = datetime.now()
    
    try:
        db.commit()
        db.refresh(profile)
        return {"message": "Profile updated successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.put("/password", status_code=status.HTTP_200_OK)
async def update_password(
    db: db_dependency,
    password_data: PasswordUpdateRequest,
    current_user: user_dependency
):
    # Get user
    user = db.query(User).filter(User.id == current_user['id']).first()
    
    # Verify current password
    if not bcrypt_context.verify(password_data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect"
        )
    
    # Update password
    user.hashed_password = bcrypt_context.hash(password_data.new_password)
    user.has_changed_password = True
    user.initial_password = None
    
    try:
        db.commit()
        return {"message": "Password updated successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/initial-password/{user_id}")
async def get_initial_password(
    user_id: int,
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.has_changed_password or not user.initial_password:
        raise HTTPException(
            status_code=400,
            detail="Initial password is no longer available"
        )
    
    return {
        "username": user.username,
        "initial_password": user.initial_password
    }

@router.get("/users")
async def get_users(
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    try:
        users = db.query(User).options(
            joinedload(User.profile),
            joinedload(User.branch)
        ).all()
        
        return [
            {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "branch_id": user.branch_id,
                "has_changed_password": user.has_changed_password,
                "created_at": user.created_at.isoformat() if hasattr(user, 'created_at') and user.created_at else None,
                "profile": {
                    "first_name": user.profile.first_name,
                    "last_name": user.profile.last_name,
                    "email": user.profile.email,
                    "phone_number": user.profile.phone_number,
                    "license_number": user.profile.license_number
                } if user.profile else None,
                "branch": {
                    "id": user.branch.id,
                    "branch_name": user.branch.branch_name,
                    "branch_type": user.branch.branch_type
                } if user.branch else None
            }
            for user in users
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.put("/initial-credentials", status_code=status.HTTP_200_OK)
async def update_initial_credentials(
    db: db_dependency,
    credentials_data: InitialCredentialsUpdateRequest,
    current_user: user_dependency
):
    # Get user
    user = db.query(User).filter(User.id == current_user['id']).first()
    
    # Verify current password
    if not bcrypt_context.verify(credentials_data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect"
        )
    
    # Check if new username already exists
    existing_user = db.query(User).filter(
        User.username == credentials_data.new_username,
        User.id != current_user['id']
    ).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
    
    try:
        # Update both username and password
        user.username = credentials_data.new_username
        user.hashed_password = bcrypt_context.hash(credentials_data.new_password)
        user.has_changed_password = True
        user.initial_password = None
        
        db.commit()
        
        # Generate new token with updated username
        new_token = create_access_token(
            user.username,
            user.id,
            UserRole(user.role),
            user.branch_id,
            timedelta(hours=12)
        )
        
        return {
            "message": "Credentials updated successfully",
            "access_token": new_token,
            "token_type": "bearer"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/reset-password/{user_id}", status_code=status.HTTP_200_OK)
async def reset_password(
    user_id: int,
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    # Get user
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Generate new random password
    new_password = ''.join(__import__('random').choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=10))
    
    # Update password
    user.hashed_password = bcrypt_context.hash(new_password)
    user.has_changed_password = False
    user.initial_password = new_password
    
    try:
        db.commit()
        return {
            "message": "Password reset successfully",
            "username": user.username,
            "new_password": new_password
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )