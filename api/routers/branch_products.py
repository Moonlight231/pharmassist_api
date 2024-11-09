from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Annotated
from pydantic import BaseModel
from datetime import date

from api.models import BranchProduct, Branch, Product, UserRole
from api.deps import db_dependency, role_required
from sqlalchemy.orm import joinedload
import sqlalchemy as sa
from api.models import ProductBatch

router = APIRouter(
    prefix='/branch-products',
    tags=['branch products']
)

class BranchProductBase(BaseModel):
    product_id: int
    branch_id: int
    quantity: int

class BranchProductCreate(BranchProductBase):
    pass

class BranchProductUpdate(BaseModel):
    expiration_date: Optional[date] = None

class BranchProductResponse(BranchProductBase):
    peso_value: float
    current_expiration_date: Optional[date]

    class Config:
        from_attributes = True

@router.post('/', response_model=BranchProductResponse, status_code=status.HTTP_201_CREATED)
def create_branch_product(
    branch_product: BranchProductCreate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    db_branch_product = BranchProduct(**branch_product.dict())
    db.add(db_branch_product)
    db.commit()
    db.refresh(db_branch_product)
    return db_branch_product

@router.get('/', response_model=List[BranchProductResponse])
def get_branch_products(
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
    branch_id: Optional[int] = None,
    product_id: Optional[int] = None
):
    # First get active batches with their quantities
    batch_totals = db.query(
        ProductBatch.branch_id,
        ProductBatch.product_id,
        sa.func.sum(ProductBatch.quantity).label('total_quantity')
    ).filter(
        ProductBatch.is_active == True
    ).group_by(
        ProductBatch.branch_id,
        ProductBatch.product_id
    ).subquery()
    
    # Join with branch products
    query = db.query(BranchProduct).options(
        joinedload(BranchProduct.batches)
    ).outerjoin(
        batch_totals,
        sa.and_(
            BranchProduct.branch_id == batch_totals.c.branch_id,
            BranchProduct.product_id == batch_totals.c.product_id
        )
    )
    
    # Apply filters
    if user['role'] == UserRole.PHARMACIST.value:
        query = query.filter(BranchProduct.branch_id == user['branch_id'])
    elif branch_id:
        query = query.filter(BranchProduct.branch_id == branch_id)
        
    if product_id:
        query = query.filter(BranchProduct.product_id == product_id)
    
    branch_products = query.all()
    
    # Update quantities from the subquery results
    for bp in branch_products:
        bp.quantity = db.query(sa.func.sum(ProductBatch.quantity))\
            .filter(
                ProductBatch.branch_id == bp.branch_id,
                ProductBatch.product_id == bp.product_id,
                ProductBatch.is_active == True
            ).scalar() or 0
    
    return branch_products

@router.put('/{branch_id}/{product_id}', response_model=BranchProductResponse)
def update_branch_product(
    branch_id: int,
    product_id: int,
    branch_product: BranchProductUpdate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))]
):
    # Check if pharmacist is assigned to this branch
    if user['role'] == UserRole.PHARMACIST and user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only modify products in your assigned branch"
        )

    db_branch_product = db.query(BranchProduct).filter(
        BranchProduct.branch_id == branch_id,
        BranchProduct.product_id == product_id
    ).first()
    
    if not db_branch_product:
        raise HTTPException(status_code=404, detail="Branch product not found")
    
    for key, value in branch_product.dict(exclude_unset=True).items():
        setattr(db_branch_product, key, value)
    
    db.commit()
    db.refresh(db_branch_product)
    return db_branch_product

@router.delete('/{branch_id}/{product_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_branch_product(
    branch_id: int,
    product_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    db_branch_product = db.query(BranchProduct).filter(
        BranchProduct.branch_id == branch_id,
        BranchProduct.product_id == product_id
    ).first()
    if not db_branch_product:
        raise HTTPException(status_code=404, detail="Branch product not found")
    db.delete(db_branch_product)
    db.commit()
    return {"detail": "Branch product deleted successfully"}