from pydantic import BaseModel
from typing import Optional, Annotated, List
from fastapi import APIRouter, HTTPException, status, Depends

from api.models import Product, UserRole
from api.deps import db_dependency, user_dependency, role_required

router = APIRouter(
    prefix='/products',
    tags=['products']
)

class ProductBase(BaseModel):
    name: str
    category: str
    cost: float
    srp: float

class AddProduct(ProductBase):
    pass

class UpdateProduct(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    cost: Optional[float] = None
    srp: Optional[float] = None

@router.get('/')
def get_product(db: db_dependency, user: user_dependency, product_id: int):
    # Allow all authenticated users to view products
    return db.query(Product).filter(Product.id == product_id).first()

@router.get('/products')
def get_products(db: db_dependency, user: user_dependency):
    # Allow all authenticated users to view products
    return db.query(Product).all()

@router.post('/', status_code=status.HTTP_201_CREATED)
def add_product(
    db: db_dependency, 
    product: AddProduct, 
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    # Only ADMIN can add products
    db_product = Product(**product.model_dump())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

@router.put('/{product_id}', status_code=status.HTTP_200_OK)
def update_product(
    product_id: int,
    product: UpdateProduct,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    # Only ADMIN can update products
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")
    
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
    # Only ADMIN can delete products
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(db_product)
    db.commit()
    return {"detail": "Product deleted successfully"}