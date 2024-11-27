from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, desc
from datetime import datetime, timedelta, date
from typing import List, Optional, Annotated
from pydantic import BaseModel

from api.deps import db_dependency, role_required
from api.models import (
    Expense, 
    Product, 
    Branch, 
    InvReport, 
    AnalyticsTimeSeries, 
    UserRole, 
    BranchProduct,
    InvReportItem,
    ProductBatch,
    BranchType
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


class TimeSeriesData(BaseModel):
    timestamp: datetime
    value: float

class ProductPerformance(BaseModel):
    product_id: int
    product_name: str
    total_quantity: int
    total_revenue: float
    profit_margin: float

class BranchPerformance(BaseModel):
    branch_id: int
    branch_name: str
    total_sales: float
    total_expenses: float
    profit: float
    performance_metrics: dict

class CompanyAnalytics(BaseModel):
    total_revenue: float
    total_expenses: float
    gross_profit: float
    net_profit: float
    profit_margin: float
    branch_performance: List[BranchPerformance]
    top_products: List[ProductPerformance]
    revenue_trend: List[TimeSeriesData]
    expense_trend: List[TimeSeriesData]
    profit_trend: List[TimeSeriesData]

@router.get("/", response_model=CompanyAnalytics)
async def get_company_analytics(
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN]))],
    time_range: str = "30d"
):
    # Calculate date range
    end_date = datetime.now()
    if time_range == "7d":
        start_date = end_date - timedelta(days=7)
        prev_start_date = start_date - timedelta(days=7)
    elif time_range == "30d":
        start_date = end_date - timedelta(days=30)
        prev_start_date = start_date - timedelta(days=30)
    elif time_range == "90d":
        start_date = end_date - timedelta(days=90)
        prev_start_date = start_date - timedelta(days=90)
    else:  # 1y
        start_date = end_date - timedelta(days=365)
        prev_start_date = start_date - timedelta(days=365)

    # Get sales data from inventory reports
    sales_data = db.query(
        InvReport.branch_id,
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('total_sales'),
        func.sum(
            InvReportItem.offtake * 
            (InvReportItem.current_srp - InvReportItem.current_cost)
        ).label('total_profit')
    ).join(
        InvReportItem, 
        InvReport.id == InvReportItem.invreport_id
    ).filter(
        InvReport.end_date >= start_date,
        InvReport.end_date <= end_date
    ).group_by(InvReport.branch_id).all()

    # Get previous period data
    prev_sales_data = db.query(
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('total_sales')
    ).join(
        InvReport,
        InvReportItem.invreport_id == InvReport.id
    ).filter(
        InvReport.end_date >= prev_start_date,
        InvReport.end_date < start_date
    ).scalar() or 0

    # Get expense data
    expense_data = db.query(
        Expense.branch_id,
        func.sum(Expense.amount).label('total_expenses')
    ).filter(
        Expense.date_created >= start_date,
        Expense.date_created <= end_date
    ).group_by(Expense.branch_id).all()

    # Calculate metrics
    total_revenue = sum(sale.total_sales for sale in sales_data)
    total_expenses = sum(expense.total_expenses for expense in expense_data)
    gross_profit = sum(sale.total_profit for sale in sales_data)
    net_profit = gross_profit - total_expenses
    profit_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0

    # Record daily metrics
    AnalyticsTimeSeries.record_metric(db, "revenue", total_revenue)
    AnalyticsTimeSeries.record_metric(db, "expenses", total_expenses)
    AnalyticsTimeSeries.record_metric(db, "profit", net_profit)

    # Get branch performance
    branches = db.query(Branch).all()
    branch_performance = []
    for branch in branches:
        branch_sales = next((s for s in sales_data if s.branch_id == branch.id), None)
        branch_expenses = next((e for e in expense_data if e.branch_id == branch.id), None)
        
        performance = {
            "branch_id": branch.id,
            "branch_name": branch.branch_name,
            "total_sales": branch_sales.total_sales if branch_sales else 0,
            "total_expenses": branch_expenses.total_expenses if branch_expenses else 0,
            "profit": (branch_sales.total_profit if branch_sales else 0) - 
                     (branch_expenses.total_expenses if branch_expenses else 0),
            "performance_metrics": {
                "sales_growth": calculate_growth(
                    prev_sales_data,
                    branch_sales.total_sales if branch_sales else 0
                ),
                "profit_margin": calculate_profit_margin_percentage(
                    branch_sales.total_profit if branch_sales else 0,
                    branch_sales.total_sales if branch_sales else 0
                ),
                "expense_ratio": calculate_expense_ratio(
                    branch_expenses.total_expenses if branch_expenses else 0,
                    branch_sales.total_sales if branch_sales else 0
                )
            }
        }
        branch_performance.append(performance)

        # Record branch-specific metrics
        if branch_sales:
            AnalyticsTimeSeries.record_metric(
                db, 
                "branch_revenue", 
                branch_sales.total_sales,
                branch_id=branch.id
            )
        
        if branch_expenses:
            AnalyticsTimeSeries.record_metric(
                db, 
                "branch_expenses", 
                branch_expenses.total_expenses,
                branch_id=branch.id
            )

    # Get product analytics
    product_performance = db.query(
        Product.id,
        Product.name,
        func.sum(InvReportItem.offtake).label('total_quantity'),
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('total_revenue'),
        func.sum(InvReportItem.offtake * InvReportItem.current_cost).label('total_cost')
    ).join(
        InvReportItem, 
        Product.id == InvReportItem.product_id
    ).join(
        InvReport,
        InvReport.id == InvReportItem.invreport_id
    ).filter(
        InvReport.end_date >= start_date,
        InvReport.end_date <= end_date
    ).group_by(Product.id).order_by(desc('total_revenue')).limit(10).all()

    # Record product metrics
    for product in product_performance:
        AnalyticsTimeSeries.record_metric(
            db,
            "product_revenue",
            product.total_revenue,
            product_id=product.id
        )

    # Get time series data
    time_series = get_time_series_data(db, start_date, end_date)

    return {
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
        "profit_margin": profit_margin,
        "branch_performance": branch_performance,
        "top_products": [
            {
                "product_id": p.id,
                "product_name": p.name,
                "total_quantity": p.total_quantity,
                "total_revenue": p.total_revenue,
                "profit_margin": calculate_profit_margin(p)
            }
            for p in product_performance
        ],
        "revenue_trend": time_series["revenue"],
        "expense_trend": time_series["expenses"],
        "profit_trend": time_series["profit"]
    }

def get_time_series_data(db: db_dependency, start_date: datetime, end_date: datetime):
    """Get time series data for revenue, expenses, and profit"""
    revenue_data = db.query(
        func.date_trunc('day', InvReport.end_date).label('date'),
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('value')
    ).join(
        InvReportItem,
        InvReport.id == InvReportItem.invreport_id
    ).filter(
        InvReport.end_date >= start_date,
        InvReport.end_date <= end_date
    ).group_by('date').order_by('date').all()

    expense_data = db.query(
        func.date_trunc('day', Expense.date_created).label('date'),
        func.sum(Expense.amount).label('value')
    ).filter(
        Expense.date_created >= start_date,
        Expense.date_created <= end_date
    ).group_by('date').order_by('date').all()

    # Calculate daily profit
    profit_trend = []
    for date in (start_date + timedelta(n) for n in range((end_date - start_date).days + 1)):
        daily_revenue = next((r.value for r in revenue_data if r.date.date() == date.date()), 0)
        daily_expense = next((e.value for e in expense_data if e.date.date() == date.date()), 0)
        profit_trend.append({
            "timestamp": date,
            "value": daily_revenue - daily_expense
        })

    return {
        "revenue": [{"timestamp": r.date, "value": r.value} for r in revenue_data],
        "expenses": [{"timestamp": e.date, "value": e.value} for e in expense_data],
        "profit": profit_trend
    }

def calculate_profit_margin(product_data) -> float:
    """Calculate profit margin for a product"""
    if not hasattr(product_data, 'total_revenue') or product_data.total_revenue == 0:
        return 0
    cost = getattr(product_data, 'total_cost', 0)
    return ((product_data.total_revenue - cost) / product_data.total_revenue) * 100

@router.get("/inventory")
async def get_inventory_analytics(
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
    branch_id: Optional[int] = None,
    days: int = 30
):
    """Get inventory analytics focusing on stock levels and expiry"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    query = db.query(BranchProduct).join(Product)
    if branch_id:
        query = query.filter(BranchProduct.branch_id == branch_id)
    
    return {
        "expiring_products": get_expiring_products(db, days),
        "low_stock_items": get_low_stock_items(db, branch_id),
        "inventory_value": calculate_inventory_value(query.all())
    }

def get_expiring_products(db: db_dependency, days: int):
    """Get products nearing expiration"""
    today = date.today()
    expiry_date = today + timedelta(days=days)
    
    query = (
        db.query(ProductBatch, Product)
        .select_from(ProductBatch)
        .join(Product, ProductBatch.product_id == Product.id)
        .filter(
            ProductBatch.expiration_date <= expiry_date,
            ProductBatch.expiration_date >= today,
            ProductBatch.is_active == True,
            ProductBatch.quantity > 0
        )
    )
    
    return [
        {
            "product_id": batch.product_id,
            "product_name": product.name,
            "quantity": batch.quantity,
            "expiration_date": batch.expiration_date,
            "days_until_expiry": (batch.expiration_date - today).days
        }
        for batch, product in query.all()
    ]

def get_low_stock_items(db: db_dependency, branch_id: Optional[int] = None):
    """Get items with stock below threshold"""
    query = (
        db.query(BranchProduct)
        .join(Product)
        .join(Branch)
        .filter(BranchProduct.is_available == True)
    )
    
    if branch_id:
        query = query.filter(BranchProduct.branch_id == branch_id)
    
    low_stock_items = []
    for bp in query.all():
        threshold = (bp.product.retail_low_stock_threshold 
                   if bp.branch.branch_type == BranchType.RETAIL.value 
                   else bp.product.wholesale_low_stock_threshold)
        if bp.quantity <= threshold:
            low_stock_items.append({
                "product_id": bp.product_id,
                "product_name": bp.product.name,
                "current_stock": bp.quantity,
                "threshold": threshold
            })
    return low_stock_items

def calculate_inventory_value(branch_products):
    """Calculate total inventory value"""
    return sum(bp.quantity * bp.product.cost for bp in branch_products)

def calculate_growth(previous: float, current: float) -> float:
    """Calculate percentage growth"""
    if previous == 0:
        return 0
    return ((current - previous) / previous) * 100

def calculate_profit_margin_percentage(profit: float, sales: float) -> float:
    """Calculate profit margin as percentage"""
    if sales == 0:
        return 0
    return (profit / sales) * 100

def calculate_expense_ratio(expenses: float, sales: float) -> float:
    """Calculate expense to sales ratio"""
    if sales == 0:
        return 0
    return (expenses / sales) * 100

@router.get("/branch/{branch_id}")
async def get_branch_analytics(
    branch_id: int,
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
    time_range: str = "30d",
    granularity: str = "daily"  # Can be 'daily', 'weekly', 'monthly', 'yearly'
):
    # Calculate date range
    end_date = datetime.now()
    if time_range == "7d":
        start_date = end_date - timedelta(days=7)
    elif time_range == "30d":
        start_date = end_date - timedelta(days=30)
    elif time_range == "90d":
        start_date = end_date - timedelta(days=90)
    elif time_range == "1y":
        start_date = end_date - timedelta(days=365)
    else:
        # Custom date range can be added here
        start_date = end_date - timedelta(days=30)

    # Get all sales data points
    sales_data = db.query(
        InvReport.created_at,
        InvReportItem.offtake,
        InvReportItem.current_srp,
        InvReportItem.current_cost,
        Product.name.label('product_name')
    ).join(
        Product,
        Product.id == InvReportItem.product_id
    ).join(
        InvReport,
        InvReport.id == InvReportItem.invreport_id
    ).filter(
        InvReport.branch_id == branch_id,
        InvReport.created_at >= start_date,
        InvReport.created_at <= end_date
    ).all()

    # Get all expense data points
    expense_data = db.query(
        Expense.date_created,
        Expense.amount,
        Expense.type,
        Expense.name
    ).filter(
        Expense.branch_id == branch_id,
        Expense.date_created >= start_date,
        Expense.date_created <= end_date
    ).all()

    return {
        "sales": [
            {
                "date": sale.created_at,
                "product": sale.product_name,
                "quantity": sale.offtake,
                "revenue": float(sale.offtake * sale.current_srp),
                "cost": float(sale.offtake * sale.current_cost),
                "profit": float(sale.offtake * (sale.current_srp - sale.current_cost))
            }
            for sale in sales_data
        ],
        "expenses": [
            {
                "date": expense.date_created,
                "amount": float(expense.amount),
                "type": expense.type,
                "description": expense.name
            }
            for expense in expense_data
        ]
    }

