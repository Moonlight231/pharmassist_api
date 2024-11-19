from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field, computed_field
from datetime import datetime, date, timedelta

from api.models import Transaction, TransactionItem, Client, BranchProduct, ProductBatch, UserRole
from api.deps import db_dependency, role_required

router = APIRouter(
    prefix='/transactions',
    tags=['transactions']
)

# Schemas
class TransactionItemBase(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)

class TransactionCreate(BaseModel):
    client_id: int
    transaction_terms: Optional[int] = None
    transaction_markup: Optional[float] = Field(ge=0, le=1, default=None)
    initial_payment: Optional[float] = Field(ge=0, default=0)
    items: List[TransactionItemBase]

class TransactionItemResponse(BaseModel):
    id: int
    product_id: int
    quantity: int
    base_price: float
    markup_price: float
    total_amount: float

    model_config = {
        "from_attributes": True
    }

class TransactionResponse(BaseModel):
    id: int
    reference_number: str
    client_id: int
    total_amount: float
    amount_paid: float
    payment_status: str
    transaction_date: datetime
    due_date: date
    transaction_terms: int
    transaction_markup: float
    void_reason: Optional[str]
    is_void: bool
    items: List[TransactionItemResponse]

    model_config = {
        "from_attributes": True
    }

    @computed_field
    def balance(self) -> float:
        return self.total_amount - self.amount_paid

    @computed_field
    def is_overdue(self) -> bool:
        return date.today() > self.due_date and self.payment_status != 'paid'

class PaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    payment_date: Optional[date] = None

class TransactionFilter(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    payment_status: Optional[str] = None
    is_overdue: Optional[bool] = None

class VoidTransaction(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)

# Endpoints
@router.post('/', response_model=TransactionResponse)
def create_transaction(
    transaction: TransactionCreate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))]
):
    # Get client and verify
    client = db.query(Client).filter(Client.id == transaction.client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Use provided values or fall back to client defaults
    terms = transaction.transaction_terms or client.payment_terms
    markup = transaction.transaction_markup or client.markup_percentage
    
    # Generate reference number
    reference_number = Transaction.generate_reference(db, user['branch_id'])
    
    # Create transaction
    new_transaction = Transaction(
        client_id=client.id,
        branch_id=user['branch_id'],
        reference_number=reference_number,
        transaction_terms=terms,
        transaction_markup=markup,
        payment_status='pending',
        due_date=date.today() + timedelta(days=terms)
    )
    
    total_amount = 0
    
    # Process items
    for item in transaction.items:
        # Only verify if product is available for this branch type
        branch_product = db.query(BranchProduct).filter(
            BranchProduct.branch_id == user['branch_id'],
            BranchProduct.product_id == item.product_id,
            BranchProduct.is_available == True
        ).first()
        
        if not branch_product:
            raise HTTPException(
                status_code=400,
                detail=f"Product {item.product_id} is not available for this branch"
            )
        
        # Create transaction item
        transaction_item = TransactionItem(
            product_id=item.product_id,
            quantity=item.quantity,
            base_price=branch_product.product.cost
        )
        transaction_item.calculate_prices(markup)
        
        total_amount += transaction_item.total_amount
        new_transaction.items.append(transaction_item)
    
    # Check credit limit against remaining balance after initial payment
    remaining_balance = total_amount - transaction.initial_payment
    if remaining_balance > client.available_credit:
        raise HTTPException(
            status_code=400,
            detail=f"Remaining balance exceeds available credit. Available: {client.available_credit}"
        )
    
    # Set payment status based on initial payment
    if transaction.initial_payment >= total_amount:
        payment_status = 'paid'
        amount_paid = total_amount  # Cap at total amount to prevent overpayment
    elif transaction.initial_payment > 0:
        payment_status = 'partial'
        amount_paid = transaction.initial_payment
    else:
        payment_status = 'pending'
        amount_paid = 0
    
    new_transaction.total_amount = total_amount
    new_transaction.amount_paid = amount_paid
    new_transaction.payment_status = payment_status
    
    # Update client balance with remaining amount
    client.current_balance += remaining_balance
    
    db.add(new_transaction)
    db.commit()
    db.refresh(new_transaction)
    
    return new_transaction

@router.get('/', response_model=List[TransactionResponse])
def get_transactions(
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))],
    skip: int = 0,
    limit: int = 100,
    client_id: Optional[int] = None
):
    # Following pattern from inventory_reports get endpoint
    startLine: 437
    endLine: 462
    
    query = (
        db.query(Transaction)
        .options(joinedload(Transaction.items))
        .filter(Transaction.is_void == False)
    )
    
    # Non-admin users can only see transactions from their branch
    if user['role'] == UserRole.WHOLESALER.value:
        query = query.filter(Transaction.branch_id == user['branch_id'])
    
    if client_id:
        query = query.filter(Transaction.client_id == client_id)
    
    return query.order_by(Transaction.transaction_date.desc()).offset(skip).limit(limit).all()

@router.get('/{transaction_id}', response_model=TransactionResponse)
def get_transaction(
    transaction_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))]
):
    # Following pattern from clients get endpoint
    startLine: 104
    endLine: 122
    
    transaction = db.query(Transaction).options(joinedload(Transaction.items)).filter(Transaction.id == transaction_id).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if (user['role'] == UserRole.WHOLESALER.value and 
        user['branch_id'] != transaction.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only view transactions from your branch"
        )
    
    return transaction

@router.post('/{transaction_id}/void')
def void_transaction(
    transaction_id: int,
    void_data: VoidTransaction,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))]
):
    transaction = db.query(Transaction).options(joinedload(Transaction.items)).filter(Transaction.id == transaction_id).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if transaction.is_void:
        raise HTTPException(status_code=400, detail="Transaction is already void")
    
    # Check branch access
    if (user['role'] == UserRole.WHOLESALER.value and 
        user['branch_id'] != transaction.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only void transactions from your branch"
        )
    
    # Update client balance
    transaction.client.current_balance -= (transaction.total_amount - transaction.amount_paid)
    
    transaction.void_reason = void_data.reason
    transaction.is_void = True
    db.commit()
    
    return {"detail": "Transaction voided successfully"}

@router.post('/{transaction_id}/payment')
def add_payment(
    transaction_id: int,
    payment: PaymentCreate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))]
):
    transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if transaction.is_void:
        raise HTTPException(status_code=400, detail="Cannot add payment to void transaction")
    
    # Check branch access
    if (user['role'] == UserRole.WHOLESALER.value and 
        user['branch_id'] != transaction.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only add payments to transactions from your branch"
        )
    
    # Validate payment amount
    remaining_balance = transaction.balance
    if payment.amount > remaining_balance:
        raise HTTPException(
            status_code=400,
            detail=f"Payment amount exceeds remaining balance. Remaining: {remaining_balance}"
        )
    
    # Update transaction
    transaction.amount_paid += payment.amount
    
    # Update payment status
    if transaction.amount_paid >= transaction.total_amount:
        transaction.payment_status = 'paid'
    else:
        transaction.payment_status = 'partial'
    
    # Update client balance
    transaction.client.current_balance -= payment.amount
    
    db.commit()
    db.refresh(transaction)
    
    return transaction