from pydantic import BaseModel, Field
from typing import Optional, Annotated, List
from fastapi import APIRouter, HTTPException, status, Depends

from api.models import Product, UserRole, Branch, BranchProduct, PriceHistory
from api.deps import db_dependency, user_dependency, role_required

router = APIRouter(
    prefix='/products',
    tags=['products']
)

class ProductBase(BaseModel):
    name: str
    cost: float
    srp: float
    retail_low_stock_threshold: int = Field(gt=0, default=50)
    wholesale_low_stock_threshold: int = Field(gt=0, default=50)

class AddProduct(ProductBase):
    is_retail_available: bool = True
    is_wholesale_available: bool = False

class UpdateProduct(BaseModel):
    name: Optional[str] = None
    cost: Optional[float] = None
    srp: Optional[float] = None
    retail_low_stock_threshold: Optional[int] = Field(gt=0, default=None)
    wholesale_low_stock_threshold: Optional[int] = Field(gt=0, default=None)
    is_retail_available: Optional[bool] = None
    is_wholesale_available: Optional[bool] = None

class ProductResponse(ProductBase):
    id: int
    is_retail_available: bool
    is_wholesale_available: bool
    retail_low_stock_threshold: int
    wholesale_low_stock_threshold: int

    class Config:
        from_attributes = True

@router.get('/', response_model=ProductResponse)
def get_product(db: db_dependency, user: user_dependency, product_id: int):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

@router.get('/products', response_model=List[ProductResponse])
def get_products(db: db_dependency, user: user_dependency):
    return db.query(Product).order_by(Product.name).all()

@router.post('/', status_code=status.HTTP_201_CREATED)
def add_product(
    db: db_dependency, 
    product: AddProduct, 
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    # Create the product
    db_product = Product(**product.model_dump())
    db.add(db_product)
    db.flush()  # This assigns an ID to db_product without committing
    
    # Get all branches
    branches = db.query(Branch).filter(Branch.is_active == True).all()
    
    # Create branch_products entries for each branch
    for branch in branches:
        branch_product = BranchProduct(
            branch_id=branch.id,
            product_id=db_product.id,
            quantity=0,  # Initial quantity set to 0
            is_available=False  # Explicitly set as unavailable
        )
        db.add(branch_product)
    
    try:
        db.commit()
        db.refresh(db_product)
        return db_product
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.put('/{product_id}', status_code=status.HTTP_200_OK)
def update_product(
    product_id: int,
    product: UpdateProduct,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # If cost or srp is being updated, record it in price history
    if product.cost is not None or product.srp is not None:
        price_history = PriceHistory(
            product_id=product_id,
            cost=product.cost if product.cost is not None else db_product.cost,
            srp=product.srp if product.srp is not None else db_product.srp
        )
        db.add(price_history)
    
    for key, value in product.model_dump(exclude_unset=True).items():
        setattr(db_product, key, value)
    
    db.commit()
    db.refresh(db_product)
    return db_product

@router.delete('/{product_id}')
def delete_product(
    product_id: int,
    db: db_dependency, 
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    # First check if product exists
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Get all branch products for this product
    branch_products = db.query(BranchProduct).filter(
        BranchProduct.product_id == product_id
    ).all()

    # Check if product is active in any branch and has non-zero quantity
    has_active_stock = False
    for bp in branch_products:
        if bp.is_available and bp.quantity > 0:
            has_active_stock = True
            break

    if has_active_stock:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete product while it is active and has stock in branches"
        )

    # Delete all associated branch_products first
    for bp in branch_products:
        db.delete(bp)
    
    # Then delete the product
    db.delete(db_product)
    
    try:
        db.commit()
        return {"detail": "Product deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error deleting product: {str(e)}"
        )