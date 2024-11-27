from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field, computed_field
from datetime import date, datetime, timedelta

from api.models import Expense, ExpenseScope, ExpenseType, Branch, UserRole, AnalyticsTimeSeries
from api.deps import db_dependency, role_required

router = APIRouter(
    prefix='/expenses',
    tags=['expenses']
)

# Schemas
class ExpenseBase(BaseModel):
    name: str
    type: ExpenseType
    amount: float = Field(gt=0)
    description: Optional[str] = None
    vendor: Optional[str] = None
    scope: ExpenseScope = ExpenseScope.BRANCH
    branch_id: Optional[int] = None
    date_created: Optional[date] = None

class ExpenseCreate(ExpenseBase):
    pass

class ExpenseUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[ExpenseType] = None
    amount: Optional[float] = Field(gt=0, default=None)
    description: Optional[str] = None
    vendor: Optional[str] = None
    date_created: Optional[date] = None

class BranchResponse(BaseModel):
    id: int
    name: str = Field(alias="branch_name")
    
    model_config = {
        "from_attributes": True,
        "populate_by_name": True
    }

class ExpenseResponse(ExpenseBase):
    id: int
    created_by_id: int
    created_at: datetime
    updated_at: datetime
    branch: Optional[BranchResponse] = None

    model_config = {
        "from_attributes": True
    }

class ExpenseAnalytics(BaseModel):
    total_amount: float
    daily_average: float
    highest_category: str
    highest_category_percentage: float
    month_over_month_change: float
    last_expense_date: date
    category_distribution: List[dict]

@router.post("/", response_model=ExpenseResponse, status_code=status.HTTP_201_CREATED)
def create_expense(
    expense: ExpenseCreate,
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))]
):
    db_expense = Expense(
        **expense.model_dump(),
        created_by_id=current_user['id']
    )
    db.add(db_expense)
    db.commit()
    db.refresh(db_expense)

    # Record the expense metric
    AnalyticsTimeSeries.record_metric(
        db,
        "expense",
        db_expense.amount,
        branch_id=db_expense.branch_id
    )

    return db_expense

@router.get("/", response_model=List[ExpenseResponse])
def get_expenses(
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
    skip: int = 0,
    limit: int = 100,
    branch_id: Optional[int] = None,
    scope: Optional[ExpenseScope] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
):
    query = db.query(Expense).options(joinedload(Expense.branch))
    
    if scope:
        query = query.filter(Expense.scope == scope)
    if branch_id:
        query = query.filter(Expense.branch_id == branch_id)
    if start_date:
        query = query.filter(Expense.date_created >= start_date)
    if end_date:
        query = query.filter(Expense.date_created <= end_date)
    
    if current_user['role'] != UserRole.ADMIN.value:
        query = query.filter(Expense.branch_id == current_user['branch_id'])
    
    return query.order_by(Expense.date_created.desc()).offset(skip).limit(limit).all()

@router.get("/analytics")
def get_expense_analytics(
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
    branch_id: Optional[int] = None,
    days: int = 30
):
    today = date.today()
    start_date = today - timedelta(days=days)
    last_month_start = today - timedelta(days=days*2)
    
    query = db.query(Expense)
    if current_user['role'] != UserRole.ADMIN.value:
        branch_id = current_user['branch_id']
        query = query.filter(Expense.branch_id == branch_id)
    elif branch_id:
        query = query.filter(Expense.branch_id == branch_id)

    current_expenses = query.filter(Expense.date_created >= start_date).all()
    last_month_expenses = query.filter(
        Expense.date_created >= last_month_start,
        Expense.date_created < start_date
    ).all()

    # Calculate analytics
    current_total = sum(expense.amount for expense in current_expenses)
    last_month_total = sum(expense.amount for expense in last_month_expenses)
    
    month_over_month = ((current_total - last_month_total) / last_month_total * 100 
                       if last_month_total > 0 else 0)
    
    category_totals = {}
    for expense in current_expenses:
        category_totals[expense.type] = category_totals.get(expense.type, 0) + expense.amount
    
    highest_category = max(category_totals.items(), key=lambda x: x[1]) if category_totals else ("None", 0)
    highest_category_percentage = (highest_category[1] / current_total * 100 
                                 if current_total > 0 else 0)

    # Get latest expense details
    latest_expense = (
        query.filter(Expense.date_created >= start_date)
        .order_by(Expense.created_at.desc())
        .first()
    )

    return {
        "total_amount": current_total,
        "daily_average": current_total / days if days > 0 else 0,
        "highest_category": highest_category[0],
        "highest_category_percentage": highest_category_percentage,
        "month_over_month_change": month_over_month,
        "last_expense_date": latest_expense.created_at if latest_expense else datetime.now(),
        "category_distribution": [
            {"category": cat, "amount": amt} 
            for cat, amt in category_totals.items()
        ]
    }

@router.get("/{expense_id}", response_model=ExpenseResponse)
def get_expense(
    expense_id: int,
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))]
):
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    
    if (current_user['role'] != UserRole.ADMIN.value and 
        current_user['branch_id'] != expense.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only view expenses from your branch"
        )
    
    return expense

@router.put("/{expense_id}", response_model=ExpenseResponse)
def update_expense(
    expense_id: int,
    expense_update: ExpenseUpdate,
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))]
):
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    
    if (current_user['role'] != UserRole.ADMIN.value and 
        current_user['branch_id'] != expense.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only update expenses from your branch"
        )
    
    for key, value in expense_update.model_dump(exclude_unset=True).items():
        setattr(expense, key, value)
    
    db.commit()
    db.refresh(expense)
    return expense

@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(
    expense_id: int,
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))]
):
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    
    if (current_user['role'] != UserRole.ADMIN.value and 
        current_user['branch_id'] != expense.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only delete expenses from your branch"
        )
    
    db.delete(expense)
    db.commit() 