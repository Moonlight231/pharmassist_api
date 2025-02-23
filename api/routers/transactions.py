from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field, computed_field
from datetime import datetime, date, timedelta

from api.models import Transaction, TransactionItem, Client, BranchProduct, ProductBatch, UserRole, Payment
from api.deps import db_dependency, role_required

router = APIRouter(
    prefix='/transactions',
    tags=['transactions']
)

# Schemas
class TransactionItemBase(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    base_price: float = Field(gt=0)

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
        return round(self.total_amount - self.amount_paid, 2)

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

class PaymentResponse(BaseModel):
    id: int
    transaction_id: int
    client_id: int
    amount: float
    payment_date: date
    recorded_by_id: int
    created_at: datetime
    is_void: bool
    void_reason: Optional[str] = None

    model_config = {
        "from_attributes": True
    }

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
    
    # Create transactions
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
            base_price=item.base_price
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
        amount_paid = round(total_amount, 2)  # Cap at total amount to prevent overpayment
    elif transaction.initial_payment > 0:
        payment_status = 'partial'
        amount_paid = round(transaction.initial_payment, 2)
    else:
        payment_status = 'pending'
        amount_paid = 0.0
    
    new_transaction.total_amount = round(total_amount, 2)
    new_transaction.amount_paid = amount_paid
    new_transaction.payment_status = payment_status
    
    # Update client balance with remaining amount
    client.current_balance = round(client.current_balance + remaining_balance, 2)
    
    # First commit the transaction to get the ID
    db.add(new_transaction)
    db.commit()
    db.refresh(new_transaction)
    
    # Create payment record if there's an initial payment
    if amount_paid > 0:
        initial_payment = Payment(
            transaction_id=new_transaction.id,  # Now we have the transaction ID
            client_id=client.id,
            amount=amount_paid,
            payment_date=date.today(),
            recorded_by_id=user['id']
        )
        db.add(initial_payment)
        db.commit()
    
    return new_transaction

@router.get('/', response_model=List[TransactionResponse])
def get_transactions(
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))],
    skip: int = 0,
    limit: int = 100,
    client_id: Optional[int] = None,
    include_void: bool = False
):
    query = (
        db.query(Transaction)
        .options(joinedload(Transaction.items))
    )
    
    # Only filter out void transactions if include_void is False
    if not include_void:
        query = query.filter(Transaction.is_void == False)
    
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
    transaction = (
        db.query(Transaction)
        .options(
            joinedload(Transaction.items),
            joinedload(Transaction.payments)
        )
        .filter(Transaction.id == transaction_id)
        .first()
    )
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
    
    # Void all payments first
    for payment in transaction.payments:
        if not payment.is_void:
            payment.is_void = True
            payment.void_reason = f"Transaction voided: {void_data.reason}"
            # Update client balance for each payment
            transaction.client.current_balance += payment.amount
            # Update transaction amount_paid
            transaction.amount_paid -= payment.amount
    
    # Update payment status
    transaction.payment_status = 'pending'
    
    # Update client balance - only unpaid amount if any remains
    if round(transaction.balance, 2) > 0:
        transaction.client.current_balance -= round(transaction.balance, 2)
    
    transaction.void_reason = void_data.reason
    transaction.is_void = True
    db.commit()
    
    return {"detail": "Transaction voided successfully"}

@router.post('/{transaction_id}/payment', response_model=PaymentResponse)
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
    remaining_balance = round(transaction.balance, 2)
    if payment.amount > remaining_balance:
        raise HTTPException(
            status_code=400,
            detail=f"Payment amount exceeds remaining balance. Remaining: {remaining_balance}"
        )
    
    # Create payment record
    new_payment = Payment(
        transaction_id=transaction.id,
        client_id=transaction.client_id,
        amount=round(payment.amount, 2),
        payment_date=payment.payment_date or date.today(),
        recorded_by_id=user['id']
    )
    db.add(new_payment)
    
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
    db.refresh(new_payment)
    
    return new_payment

@router.post('/{transaction_id}/payment/{payment_id}/void')
def void_payment(
    transaction_id: int,
    payment_id: int,
    void_data: VoidTransaction,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))]
):
    payment = db.query(Payment).filter(
        Payment.id == payment_id,
        Payment.transaction_id == transaction_id
    ).first()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    
    # Check branch access
    if (user['role'] == UserRole.WHOLESALER.value and 
        user['branch_id'] != transaction.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only void payments from your branch"
        )
    
    # Update transaction
    transaction.amount_paid -= payment.amount
    
    # Update payment status
    if transaction.amount_paid == 0:
        transaction.payment_status = 'pending'
    else:
        transaction.payment_status = 'partial'
    
    # Update client balance
    transaction.client.current_balance += payment.amount
    
    # Soft delete the payment
    payment.is_void = True
    payment.void_reason = void_data.reason
    
    db.commit()
    
    return {"detail": "Payment voided successfully"}

@router.get('/{transaction_id}/payments', response_model=List[PaymentResponse])
def get_transaction_payments(
    transaction_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))],
    include_void: bool = False
):
    # Get transaction and verify access
    transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Check branch access
    if (user['role'] == UserRole.WHOLESALER.value and 
        user['branch_id'] != transaction.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only view payments for transactions from your branch"
        )
    
    # Query payments
    query = db.query(Payment).filter(Payment.transaction_id == transaction_id)
    
    # Exclude void payments unless specifically requested
    if not include_void:
        query = query.filter(Payment.is_void == False)
    
    return query.order_by(Payment.payment_date.desc()).all()