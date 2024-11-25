from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import Annotated, List, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta
import sqlalchemy as sa

from api.models import Branch, UserRole, Product, BranchProduct, ProductBatch
from api.deps import db_dependency, role_required

router = APIRouter(
    prefix='/branches',
    tags=['branches']
)

class BranchBase(BaseModel):
    branch_name: str
    location: str
    is_active: bool

class BranchCreate(BranchBase):
    branch_type: str = 'retail'

class BranchUpdate(BaseModel):
    branch_name: Optional[str] = None
    location: Optional[str] = None
    is_active: Optional[bool] = None

class BranchResponse(BranchBase):
    id: int
    branch_type: str
    has_low_stock: bool = False
    has_near_expiry: bool = False

    class Config:
        from_attributes = True

@router.post('/', response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
def create_branch(
    branch: BranchCreate, 
    db: db_dependency, 
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    # Create the branch
    new_branch = Branch(**branch.model_dump())
    db.add(new_branch)
    db.flush()  # This assigns an ID to new_branch without committing

    # Get all existing products
    products = db.query(Product).all()

    # Create branch_products entries for each product
    for product in products:
        branch_product = BranchProduct(
            branch_id=new_branch.id,
            product_id=product.id,
            quantity=0,  # Initial quantity set to 0
            is_available=False  # Explicitly set as unavailable
        )
        db.add(branch_product)

    db.commit()
    db.refresh(new_branch)
    return new_branch

@router.get('/', response_model=List[BranchResponse])
def get_branches(
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
):
    query = db.query(Branch)
    
    # Non-admin users can only view their own branch
    if user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value]:
        query = query.filter(Branch.id == user['branch_id'])
    
    branches = query.all()
    
    for branch in branches:
        # Get branch products that are available with their batches
        branch_products = (
            db.query(BranchProduct)
            .options(joinedload(BranchProduct.batches))
            .filter(
                BranchProduct.branch_id == branch.id,
                BranchProduct.is_available == True
            )
            .all()
        )
        
        # Check for low stock
        has_low_stock = any(bp.is_low_stock for bp in branch_products)
        
        # Check for near expiry or expired (30 days threshold)
        thirty_days_from_now = datetime.now().date() + timedelta(days=30)
        today = datetime.now().date()
        
        has_near_expiry = any(
            any(
                batch.is_active and (
                    batch.expiration_date <= thirty_days_from_now or  # Near expiry
                    batch.expiration_date <= today  # Already expired
                )
                for batch in bp.batches
            )
            for bp in branch_products
        )
        
        branch.has_low_stock = has_low_stock
        branch.has_near_expiry = has_near_expiry
    
    return branches

@router.get('/{branch_id}', response_model=BranchResponse)
def get_branch(
    branch_id: int, 
    db: db_dependency, 
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))]
):
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    
    # Non-admin users can only view their own branch
    if (user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value] and 
        user['branch_id'] != branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only view your assigned branch"
        )
    
    # Get branch products
    branch_products = (
        db.query(BranchProduct)
        .filter(
            BranchProduct.branch_id == branch_id,
            BranchProduct.is_available == True
        )
        .all()
    )

    # Check for low stock and near expiry using branch products
    has_low_stock = any(bp.is_low_stock for bp in branch_products)
    has_near_expiry = any(
        bp.current_expiration_date 
        and bp.current_expiration_date <= datetime.now().date() + timedelta(days=30)
        for bp in branch_products
    )

    # Add the status to the branch response
    branch.has_low_stock = has_low_stock
    branch.has_near_expiry = has_near_expiry
    
    return branch

@router.put('/{branch_id}', response_model=BranchResponse)
def update_branch(
    branch_id: int, 
    branch_update: BranchUpdate, 
    db: db_dependency, 
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    db_branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not db_branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    
    for key, value in branch_update.dict(exclude_unset=True).items():
        setattr(db_branch, key, value)
    
    db.commit()
    db.refresh(db_branch)
    return db_branch

@router.delete('/{branch_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_branch(
    branch_id: int, 
    db: db_dependency, 
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    db.delete(branch)
    db.commit()
    return {"detail": "Branch deleted successfully"}