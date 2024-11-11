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

    @property
    def peso_value(self) -> float:
        """Calculate peso value based on product SRP and quantity"""
        return self.product.srp * self.quantity if self.product else 0.00

class BranchProductCreate(BranchProductBase):
    pass

class BranchProductUpdate(BaseModel):
    expiration_date: Optional[date] = None

class BranchProductResponse(BranchProductBase):
    peso_value: float
    current_expiration_date: Optional[date]
    is_low_stock: bool
    active_quantity: int
    is_available: bool

    class Config:
        from_attributes = True

class LowStockProductResponse(BaseModel):
    product_id: int
    name: str
    current_quantity: int
    threshold: int
    branch_id: int
    branch_name: str
    is_available: bool

    class Config:
        from_attributes = True

class LowStockSummary(BaseModel):
    total_products: int
    low_stock_count: int
    critical_products: List[BranchProductResponse]

class AvailabilityUpdate(BaseModel):
    is_available: bool

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
    product_id: Optional[int] = None,
    low_stock_only: bool = False
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
        joinedload(BranchProduct.batches),
        joinedload(BranchProduct.product)
    ).join(Product)
    
    # Apply filters
    if user['role'] == UserRole.PHARMACIST.value:
        query = query.filter(BranchProduct.branch_id == user['branch_id'])
    elif branch_id:
        query = query.filter(BranchProduct.branch_id == branch_id)
        
    if product_id:
        query = query.filter(BranchProduct.product_id == product_id)
    
    # Add ordering by product name
    query = query.order_by(Product.name)
    
    branch_products = query.all()
    
    # Update quantities and filter low stock if requested
    result = []
    for bp in branch_products:
        active_quantity = sum(
            batch.quantity for batch in bp.batches 
            if batch.is_active
        )
        bp.quantity = active_quantity
        
        if not low_stock_only or bp.is_low_stock:
            result.append(bp)
    
    return result

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

@router.get('/low-stock/{branch_id}', response_model=List[LowStockProductResponse])
def get_low_stock_products(
    branch_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
):
    """Get products that are below their low stock threshold"""
    
    # Check if pharmacist is assigned to this branch
    if user['role'] == UserRole.PHARMACIST.value and user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only view products in your assigned branch"
        )

    # Get all branch products with their related data
    query = (
        db.query(
            Product,
            Branch,
            BranchProduct,
            sa.func.sum(ProductBatch.quantity).label('current_quantity')
        )
        .join(BranchProduct, sa.and_(
            BranchProduct.product_id == Product.id,
            BranchProduct.branch_id == branch_id
        ))
        .join(Branch, Branch.id == BranchProduct.branch_id)
        .outerjoin(ProductBatch, sa.and_(
            ProductBatch.product_id == Product.id,
            ProductBatch.branch_id == branch_id,
            ProductBatch.is_active == True
        ))
        .filter(BranchProduct.is_available == True)
        .group_by(Product.id, Branch.id, BranchProduct.product_id, BranchProduct.branch_id)
        .having(
            sa.or_(
                sa.func.sum(ProductBatch.quantity) <= Product.low_stock_threshold,
                sa.func.sum(ProductBatch.quantity) == None
            )
        )
        .order_by(
            # Order by how close to empty (percentage of threshold remaining)
            (sa.func.coalesce(sa.func.sum(ProductBatch.quantity), 0) / Product.low_stock_threshold).asc(),
            Product.name.asc()
        )
    )

    results = query.all()
    
    return [
        {
            "product_id": product.id,
            "name": product.name,
            "current_quantity": int(quantity or 0),
            "threshold": product.low_stock_threshold,
            "branch_id": branch.id,
            "branch_name": branch.branch_name,
            "is_available": branch_product.is_available
        }
        for product, branch, branch_product, quantity in results
    ]

@router.get('/low-stock-summary/{branch_id}', response_model=LowStockSummary)
def get_low_stock_summary(
    branch_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
):
    """Get a summary of low stock products for a branch"""
    
    # Check if pharmacist is assigned to this branch
    if user['role'] == UserRole.PHARMACIST.value and user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only view products in your assigned branch"
        )
    
    # Get all branch products with their batches
    query = (
        db.query(BranchProduct)
        .options(
            joinedload(BranchProduct.batches),
            joinedload(BranchProduct.product)
        )
        .join(Product)
        .outerjoin(ProductBatch, sa.and_(
            ProductBatch.product_id == BranchProduct.product_id,
            ProductBatch.branch_id == BranchProduct.branch_id,
            ProductBatch.is_active == True
        ))
        .filter(BranchProduct.branch_id == branch_id)
        .group_by(
            BranchProduct.product_id,
            BranchProduct.branch_id,
            BranchProduct.quantity,
            BranchProduct.is_available,
            Product.id,
            Product.name,
            Product.low_stock_threshold
        )
        .order_by(
            (sa.func.coalesce(sa.func.sum(ProductBatch.quantity), 0) / sa.cast(Product.low_stock_threshold, sa.Numeric)).asc(),
            Product.name.asc()
        )
    )

    branch_products = query.all()
    
    # Process products
    total_products = len(branch_products)
    low_stock_products = []
    
    for bp in branch_products:
        # Update quantity to match active batches
        bp.quantity = bp.active_quantity
        
        if bp.is_low_stock:
            low_stock_products.append(bp)
    
    return {
        "total_products": total_products,
        "low_stock_count": len(low_stock_products),
        "critical_products": low_stock_products
    }

@router.patch('/{branch_id}/{product_id}/availability')
def update_product_availability(
    branch_id: int,
    product_id: int,
    availability: AvailabilityUpdate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
):
    if user['role'] == UserRole.PHARMACIST.value and user['branch_id'] != branch_id:
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
    
    db_branch_product.is_available = availability.is_available
    db.commit()
    db.refresh(db_branch_product)
    return db_branch_product