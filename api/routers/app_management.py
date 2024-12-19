from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
import os
import shutil
from datetime import datetime

from api.models import AppVersion, UserRole
from api.deps import db_dependency, role_required

router = APIRouter(
    prefix='/app-management',
    tags=['app-management']
)

UPLOAD_DIR = "static/apk_files"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

class AppVersionBase(BaseModel):
    version_name: str
    version_code: int
    release_notes: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "version_name": "1.0.0",
                    "version_code": 1,
                    "release_notes": "Initial release"
                }
            ]
        }
    }

class AppVersionResponse(AppVersionBase):
    id: int
    apk_file_path: str
    is_active: bool
    created_at: datetime

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "examples": [
                {
                    "id": 1,
                    "version_name": "1.0.0",
                    "version_code": 1,
                    "apk_file_path": "/apk_files/pomona_v1.0.0.apk",
                    "release_notes": "Initial release",
                    "is_active": True,
                    "created_at": "2024-03-20T12:00:00"
                }
            ]
        }
    }

@router.post("/upload", response_model=AppVersionResponse)
async def upload_apk(
    db: db_dependency,
    apk_file: UploadFile = File(...),
    version_name: str = Form(...),
    version_code: str = Form(...),
    release_notes: Optional[str] = Form(None),
    user: dict = Depends(role_required(UserRole.ADMIN))
):
    file_path = None
    try:
        # Validate version code
        try:
            version_code = int(version_code)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Version code must be a valid integer"
            )

        # Validate version name format (e.g., "1.0.0")
        if not version_name.replace(".", "").isdigit():
            raise HTTPException(
                status_code=400,
                detail="Version name must be in format X.Y.Z (e.g., 1.0.0)"
            )

        # Check if version code already exists
        existing_version = db.query(AppVersion).filter(
            AppVersion.version_code == version_code
        ).first()
        if existing_version:
            raise HTTPException(
                status_code=400,
                detail=f"Version code {version_code} already exists"
            )

        # Check if version name already exists
        existing_version = db.query(AppVersion).filter(
            AppVersion.version_name == version_name
        ).first()
        if existing_version:
            raise HTTPException(
                status_code=400,
                detail=f"Version name {version_name} already exists"
            )

        # Validate file type
        if not apk_file.filename.endswith('.apk'):
            raise HTTPException(
                status_code=400,
                detail="File must be an APK"
            )
        
        # Generate unique filename
        filename = f"pomona_v{version_name}.apk"
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        # Check if file already exists
        if os.path.exists(file_path):
            raise HTTPException(
                status_code=400,
                detail="A version with this name already exists"
            )
        
        # Save file
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(apk_file.file, buffer)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save file: {str(e)}"
            )
            
        # Database operations
        try:
            # Deactivate current active version if exists
            current_active = db.query(AppVersion).filter(AppVersion.is_active == True).first()
            if current_active:
                current_active.is_active = False
                
            # Create new version
            new_version = AppVersion(
                version_name=version_name,
                version_code=version_code,
                apk_file_path=f"/apk_files/{filename}",
                release_notes=release_notes,
                is_active=True,
                created_by_id=user['id']
            )
            
            db.add(new_version)
            db.commit()
            db.refresh(new_version)
            
            return new_version
            
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Database error: {str(e)}"
            )
            
    except HTTPException as he:
        # Clean up file if it was created
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        raise he
    except Exception as e:
        # Clean up file if it was created
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@router.get("/versions", response_model=List[AppVersionResponse])
async def get_versions(
    db: db_dependency,
    user: dict = Depends(role_required(UserRole.ADMIN))
):
    return db.query(AppVersion).order_by(AppVersion.created_at.desc()).all()

@router.get("/active-version")
async def get_active_version(db: db_dependency):
    version = db.query(AppVersion).filter(AppVersion.is_active == True).first()
    if not version:
        raise HTTPException(status_code=404, detail="No active version found")
    return version 