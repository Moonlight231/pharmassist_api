from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Annotated, List, Optional
from pydantic import BaseModel

from api.models import Branch, UserRole, Product, BranchProduct
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
def get_all_branches(
    db: db_dependency, 
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    branches = db.query(Branch).all()
    return branches

@router.get('/{branch_id}', response_model=BranchResponse)
def get_branch(
    branch_id: int, 
    db: db_dependency, 
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
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