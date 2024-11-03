from typing import Annotated
from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from jose import jwt, JWTError
from dotenv import load_dotenv
import os
from .database import SessionLocal
from .models import UserRole


load_dotenv()

SECRET_KEY = os.getenv('AUTH_SECRET_KEY')
ALGORITHM = os.getenv('AUTH_ALGORITHM')

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]

bcrypt_context = CryptContext(schemes=['bcrypt'], deprecated='auto')
oauth2_bearer = OAuth2PasswordBearer(tokenUrl='auth/token')
oauth2_bearer_dependency = Annotated[str, Depends(oauth2_bearer)]

async def get_current_user(token: oauth2_bearer_dependency):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get('sub')
        user_id: int = payload.get('id')
        role: str = payload.get('role')
        branch_id: int = payload.get('branch_id')
        if username is None or user_id is None or role is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Could not validate user')
        return {
            'username': username,
            'id': user_id,
            'role': role,
            'branch_id': branch_id
        }
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Could not validate user')

user_dependency = Annotated[dict, Depends(get_current_user)]

def role_required(allowed_roles):
    def inner(user: Annotated[dict, Depends(get_current_user)]):
        if isinstance(allowed_roles, list):
            if user['role'] not in allowed_roles:
                raise HTTPException(status_code=403, detail="Operation not permitted")
        elif user['role'] != allowed_roles:
            raise HTTPException(status_code=403, detail="Operation not permitted")
        return user
    return inner