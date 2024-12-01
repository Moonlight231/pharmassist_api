from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field, ConfigDict
from datetime import date, datetime

from api.models import BranchProduct, Branch, Product, UserRole, BranchType
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
    id: str
    peso_value: float
    current_expiration_date: Optional[date]
    is_low_stock: bool
    active_quantity: int
    is_available: bool
    branch_type: str
    is_retail_available: bool
    is_wholesale_available: bool
    retail_low_stock_threshold: int
    wholesale_low_stock_threshold: int
    product_name: str
    days_in_low_stock: int
    low_stock_since: Optional[datetime]

    model_config = ConfigDict(
        from_attributes=True
    )

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
    # Get branch type
    branch = db.query(Branch).filter(Branch.id == branch_product.branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    # Get product and check availability for branch type
    product = db.query(Product).filter(Product.id == branch_product.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if branch.branch_type == 'wholesale' and not product.is_wholesale_available:
        raise HTTPException(
            status_code=400,
            detail="This product is not available for wholesale branches"
        )
    elif branch.branch_type == 'retail' and not product.is_retail_available:
        raise HTTPException(
            status_code=400,
            detail="This product is not available for retail branches"
        )

    db_branch_product = BranchProduct(**branch_product.dict())
    db.add(db_branch_product)
    db.commit()
    db.refresh(db_branch_product)
    return db_branch_product

@router.get('/', response_model=List[BranchProductResponse])
def get_branch_products(
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
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
    query = (
        db.query(
            BranchProduct,
            Product,
            Branch
        )
        .join(Product)
        .join(Branch)
        .options(
            joinedload(BranchProduct.batches),
            joinedload(BranchProduct.product),
            joinedload(BranchProduct.branch)
        )
    )
    
    # Apply filters
    if user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value]:
        query = query.filter(BranchProduct.branch_id == user['branch_id'])
    elif branch_id:
        query = query.filter(BranchProduct.branch_id == branch_id)
        
    if product_id:
        query = query.filter(BranchProduct.product_id == product_id)
    
    # Get branch type and filter products accordingly
    if branch_id:
        branch = db.query(Branch).filter(Branch.id == branch_id).first()
        if branch:
            query = query.filter(
                sa.case(
                    (branch.branch_type == 'wholesale', Product.is_wholesale_available),
                    else_=Product.is_retail_available
                )
            )
    
    # Add ordering by product name
    query = query.order_by(Product.name)
    
    results = query.all()
    
    # Update quantities and filter low stock if requested
    response = []
    for bp, product, branch in results:
        active_quantity = sum(
            batch.quantity for batch in bp.batches 
            if batch.is_active
        )
        bp.quantity = active_quantity
        
        if not low_stock_only or bp.is_low_stock:
            response_item = {
                "id": f"{bp.branch_id}-{bp.product_id}",
                "product_id": bp.product_id,
                "branch_id": bp.branch_id,
                "quantity": bp.quantity,
                "peso_value": bp.peso_value,
                "current_expiration_date": bp.current_expiration_date,
                "is_low_stock": bp.is_low_stock,
                "active_quantity": active_quantity,
                "is_available": bp.is_available,
                "branch_type": branch.branch_type,
                "is_retail_available": product.is_retail_available,
                "is_wholesale_available": product.is_wholesale_available,
                "retail_low_stock_threshold": product.retail_low_stock_threshold,
                "wholesale_low_stock_threshold": product.wholesale_low_stock_threshold,
                "product_name": product.name,
                "days_in_low_stock": bp.days_in_low_stock,
                "low_stock_since": bp.low_stock_since
            }
            response.append(response_item)
    
    return response

@router.put('/{branch_id}/{product_id}', response_model=BranchProductResponse)
def update_branch_product(
    branch_id: int,
    product_id: int,
    branch_product: BranchProductUpdate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
):
    # Check if user is assigned to this branch
    if user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value] and user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only modify products in your assigned branch"
        )

    # Get branch and product
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Check if product is available for this branch type
    if branch.branch_type == 'wholesale' and not product.is_wholesale_available:
        raise HTTPException(
            status_code=400,
            detail="This product is not available for wholesale branches"
        )
    elif branch.branch_type == 'retail' and not product.is_retail_available:
        raise HTTPException(
            status_code=400,
            detail="This product is not available for retail branches"
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
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
):
    """Get products that are below their low stock threshold"""
    
    # Check if user is assigned to this branch
    if user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value] and user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only view products in your assigned branch"
        )

    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    threshold_column = (
        Product.wholesale_low_stock_threshold 
        if branch.branch_type == 'wholesale' 
        else Product.retail_low_stock_threshold
    )

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
        .join(Branch)
        .outerjoin(ProductBatch, sa.and_(
            ProductBatch.product_id == Product.id,
            ProductBatch.branch_id == branch_id,
            ProductBatch.is_active == True
        ))
        .filter(BranchProduct.is_available == True)
        .group_by(
            Product.id,
            Branch.id,
            BranchProduct.product_id,
            BranchProduct.branch_id,
            BranchProduct.is_available
        )
        .having(
            sa.or_(
                sa.func.sum(ProductBatch.quantity) <= threshold_column,
                sa.func.sum(ProductBatch.quantity) == None
            )
        )
        .order_by(
            (sa.func.coalesce(sa.func.sum(ProductBatch.quantity), 0) / threshold_column).asc(),
            Product.name.asc()
        )
    )

    results = query.all()
    
    return [
        {
            "product_id": product.id,
            "name": product.name,
            "current_quantity": int(quantity or 0),
            "threshold": (
                product.wholesale_low_stock_threshold 
                if branch.branch_type == 'wholesale' 
                else product.retail_low_stock_threshold
            ),
            "branch_id": branch.id,
            "branch_name": branch.branch_name,
            "is_available": branch_product.is_available,
            "low_stock_since": low_stock_since,
            "days_in_low_stock": days_in_low_stock
        }
        for product, branch, branch_product, quantity, low_stock_since, days_in_low_stock in results
    ]

@router.get('/low-stock-summary/{branch_id}', response_model=LowStockSummary)
def get_low_stock_summary(
    branch_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
):
    """Get a summary of low stock products for a branch"""
    
    # Check if user is assigned to this branch
    if user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value] and user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only view products in your assigned branch"
        )
    
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    query = (
        db.query(BranchProduct, Product, Branch)
        .options(
            joinedload(BranchProduct.batches),
            joinedload(BranchProduct.product),
            joinedload(BranchProduct.branch)
        )
        .join(Product)
        .join(Branch)
        .filter(
            BranchProduct.branch_id == branch_id,
            BranchProduct.is_available == True,
            sa.case(
                (branch.branch_type == 'wholesale', Product.is_wholesale_available),
                else_=Product.is_retail_available
            )
        )
    )

    results = query.all()
    
    total_products = len(results)
    low_stock_products = []
    
    for bp, product, branch in results:
        active_quantity = sum(
            batch.quantity for batch in bp.batches 
            if batch.is_active
        )
        bp.quantity = active_quantity
        
        if bp.is_low_stock:
            response_item = {
                "id": f"{bp.branch_id}-{bp.product_id}",
                "product_id": bp.product_id,
                "branch_id": bp.branch_id,
                "quantity": bp.quantity,
                "peso_value": bp.peso_value,
                "current_expiration_date": bp.current_expiration_date,
                "is_low_stock": bp.is_low_stock,
                "active_quantity": active_quantity,
                "is_available": bp.is_available,
                "branch_type": branch.branch_type,
                "is_retail_available": product.is_retail_available,
                "is_wholesale_available": product.is_wholesale_available,
                "retail_low_stock_threshold": product.retail_low_stock_threshold,
                "wholesale_low_stock_threshold": product.wholesale_low_stock_threshold,
                "product_name": product.name,
                "days_in_low_stock": bp.days_in_low_stock
            }
            low_stock_products.append(response_item)
    
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
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
):
    # Check if user is assigned to this branch
    if user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value] and user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only modify products in your assigned branch"
        )

    # Get branch and product
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Check if product is available for this branch type
    if branch.branch_type == 'wholesale' and not product.is_wholesale_available:
        raise HTTPException(
            status_code=400,
            detail="This product is not available for wholesale branches"
        )
    elif branch.branch_type == 'retail' and not product.is_retail_available:
        raise HTTPException(
            status_code=400,
            detail="This product is not available for retail branches"
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
    return {"detail": "Availability updated successfully"}