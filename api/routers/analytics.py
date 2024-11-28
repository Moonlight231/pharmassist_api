from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, desc, case, distinct
from datetime import datetime, timedelta, date
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import sqlalchemy as sa

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
    BranchType,
    PriceHistory
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

class BranchStock(BaseModel):
    id: int
    name: str
    stock: int
    is_available: bool
    branch_type: str

class StockAnalytics(BaseModel):
    total_stock: int
    branch_count: int
    low_stock_branches: int
    branch_stocks: List[BranchStock]

class ProductAnalytics(BaseModel):
    stock_analytics: StockAnalytics
    total_sales: dict = {
        "quantity": int,
        "revenue": float
    }
    current_price: dict = {
        "cost": float,
        "srp": float
    }
    price_history: List[dict] = Field(default_factory=list)
    branch_performance: List[dict] = Field(default_factory=list)
    price_analytics: dict = {
        "avg_margin": float,
        "change_count": int
    }

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

    # Get active branches count
    active_branches = db.query(func.count(Branch.id)).filter(Branch.is_active == True).scalar()

    # Get inventory status
    inventory_status = get_inventory_status(db)

    # Get sales data from inventory reports
    sales_data = db.query(
        InvReport.branch_id,
        func.sum(InvReportItem.offtake).label('total_quantity'),
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
    total_sales = sum(sale.total_quantity for sale in sales_data)
    gross_profit = sum(sale.total_profit for sale in sales_data)
    net_profit = gross_profit - total_expenses
    profit_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0

    # Get branch performance
    branches = db.query(Branch).all()
    branch_performance = []
    for branch in branches:
        branch_sales = next((s for s in sales_data if s.branch_id == branch.id), None)
        branch_expenses = next((e for e in expense_data if e.branch_id == branch.id), None)
        
        if branch_sales or branch_expenses:  # Only include branches with activity
            performance = {
                "branch_id": branch.id,
                "branch_name": branch.branch_name,
                "total_sales": branch_sales.total_quantity if branch_sales else 0,
                "revenue": branch_sales.total_sales if branch_sales else 0,
                "total_expenses": branch_expenses.total_expenses if branch_expenses else 0,
                "profit": (branch_sales.total_profit if branch_sales else 0) - 
                         (branch_expenses.total_expenses if branch_expenses else 0)
            }
            branch_performance.append(performance)

    # Get time series data
    time_series = get_time_series_data(db, start_date, end_date)

    return {
        "total_revenue": total_revenue,
        "total_sales": total_sales,
        "total_expenses": total_expenses,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
        "profit_margin": profit_margin,
        "active_branches": active_branches,
        "branch_performance": branch_performance,
        "top_products": get_top_products(db, start_date, end_date),
        "revenue_trend": time_series["revenue"],
        "expense_trend": time_series["expenses"],
        "profit_trend": time_series["profit"],
        "inventory": inventory_status
    }

def get_inventory_status(db: Session):
    """Get overall inventory status"""
    # Get total products
    total_products = db.query(func.count(Product.id)).scalar() or 0

    # Get low stock items
    low_stock_items = db.query(
        BranchProduct,
        Product,
        func.sum(ProductBatch.quantity).label('total_quantity')
    ).join(
        Product
    ).outerjoin(
        ProductBatch,
        sa.and_(
            ProductBatch.product_id == BranchProduct.product_id,
            ProductBatch.branch_id == BranchProduct.branch_id,
            ProductBatch.is_active == True
        )
    ).group_by(
        BranchProduct.product_id,
        BranchProduct.branch_id,
        Product.id
    ).having(
        sa.or_(
            func.sum(ProductBatch.quantity) <= Product.retail_low_stock_threshold,
            func.sum(ProductBatch.quantity) <= Product.wholesale_low_stock_threshold
        )
    ).all()

    # Get out of stock items
    out_of_stock = db.query(
        func.count(BranchProduct.product_id.distinct())
    ).join(
        ProductBatch,
        sa.and_(
            ProductBatch.product_id == BranchProduct.product_id,
            ProductBatch.branch_id == BranchProduct.branch_id,
            ProductBatch.is_active == True
        )
    ).filter(
        func.sum(ProductBatch.quantity) == 0
    ).scalar() or 0

    # Get near expiry items
    near_expiry_date = datetime.now() + timedelta(days=30)
    near_expiry = db.query(
        func.count(ProductBatch.id)
    ).filter(
        ProductBatch.expiration_date <= near_expiry_date,
        ProductBatch.expiration_date > datetime.now(),
        ProductBatch.is_active == True,
        ProductBatch.quantity > 0
    ).scalar() or 0

    return {
        "total_products": total_products,
        "low_stock_count": len(low_stock_items),
        "out_of_stock_count": out_of_stock,
        "near_expiry_count": near_expiry
    }

def get_top_products(db: Session, start_date: datetime, end_date: datetime, limit: int = 5):
    """Get top performing products"""
    products = db.query(
        Product.id,
        Product.name,
        func.sum(InvReportItem.offtake).label('total_quantity'),
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('revenue'),
        func.sum(
            InvReportItem.offtake * 
            (InvReportItem.current_srp - InvReportItem.current_cost)
        ).label('profit')
    ).join(
        InvReportItem
    ).join(
        InvReport
    ).filter(
        InvReport.end_date >= start_date,
        InvReport.end_date <= end_date
    ).group_by(
        Product.id
    ).order_by(
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).desc()
    ).limit(limit).all()

    return [
        {
            "id": p.id,
            "name": p.name,
            "total_sales": p.total_quantity,
            "revenue": p.revenue,
            "profit_margin": (p.profit / p.revenue * 100) if p.revenue > 0 else 0
        }
        for p in products
    ]

def get_time_series_data(db: Session, start_date: datetime, end_date: datetime) -> dict:
    """Get time series data for revenue, expenses, and profit"""
    # Get daily sales data
    sales_data = db.query(
        InvReport.created_at,
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('revenue'),
        func.sum(InvReportItem.offtake * InvReportItem.current_cost).label('cost')
    ).join(
        InvReportItem
    ).filter(
        InvReport.created_at.between(start_date, end_date)
    ).group_by(
        InvReport.created_at
    ).all()

    # Get daily expense data
    expense_data = db.query(
        Expense.date_created,
        func.sum(Expense.amount).label('amount')
    ).filter(
        Expense.date_created.between(start_date, end_date)
    ).group_by(
        Expense.date_created
    ).all()

    return {
        "revenue": [{"timestamp": s.created_at.isoformat(), "value": float(s.revenue)} for s in sales_data],
        "expenses": [{"timestamp": e.date_created.isoformat(), "value": float(e.amount)} for e in expense_data],
        "profit": [{"timestamp": s.created_at.isoformat(), "value": float(s.revenue - s.cost)} for s in sales_data]
    }

def calculate_profit_margin(product: dict) -> float:
    """Calculate profit margin for a product"""
    if product["revenue"] == 0:
        return 0
    return ((product["revenue"] - product["total_cost"]) / product["revenue"]) * 100

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
        db.query(
            BranchProduct,
            Product,
            Branch,
            sa.func.coalesce(
                sa.func.sum(
                    sa.case(
                        (ProductBatch.is_active == True, ProductBatch.quantity),
                        else_=0
                    )
                ),
                0
            ).label("active_quantity")
        )
        .join(Product)
        .join(Branch)
        .outerjoin(ProductBatch, sa.and_(
            ProductBatch.product_id == BranchProduct.product_id,
            ProductBatch.branch_id == BranchProduct.branch_id
        ))
        .filter(BranchProduct.is_available == True)
        .group_by(
            BranchProduct.product_id,
            BranchProduct.branch_id,
            Product.id,
            Branch.id,
            BranchProduct.is_available
        )
    )
    
    if branch_id:
        query = query.filter(BranchProduct.branch_id == branch_id)
    
    low_stock_items = []
    for bp, product, branch, active_quantity in query.all():
        threshold = (product.wholesale_low_stock_threshold 
                   if branch.branch_type == BranchType.WHOLESALE.value 
                   else product.retail_low_stock_threshold)
        if active_quantity <= threshold:
            low_stock_items.append({
                "product_id": bp.product_id,
                "product_name": product.name,
                "current_stock": active_quantity,
                "threshold": threshold
            })
    return low_stock_items

def calculate_inventory_value(branch_products):
    """Calculate total inventory value"""
    return sum(bp.active_quantity * bp.product.cost for bp in branch_products)

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

@router.get("/product/{product_id}", response_model=ProductAnalytics)
async def get_product_analytics(
    product_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
):
    # Get branch stocks with active quantities
    branch_products = (
        db.query(
            BranchProduct.branch_id,
            Branch.name.label("branch_name"),
            Branch.branch_type.label("branch_type"),
            BranchProduct.is_available,
            Product.wholesale_low_stock_threshold,
            Product.retail_low_stock_threshold,
            sa.func.coalesce(
                sa.func.sum(
                    sa.case(
                        (ProductBatch.is_active == True, ProductBatch.quantity),
                        else_=0
                    )
                ),
                0
            ).label("active_quantity")
        )
        .join(Branch)
        .join(Product)
        .outerjoin(ProductBatch)
        .filter(
            BranchProduct.product_id == product_id,
            BranchProduct.is_available == True,  # Only include available branch products
            sa.case(
                (Branch.branch_type == 'wholesale', Product.is_wholesale_available),
                else_=Product.is_retail_available
            )
        )
        .group_by(
            BranchProduct.branch_id,
            Branch.name,
            Branch.branch_type,
            BranchProduct.is_available,
            Product.wholesale_low_stock_threshold,
            Product.retail_low_stock_threshold
        )
        .all()
    )

    # Calculate analytics
    stock_analytics = StockAnalytics(
        total_stock=sum(bp.active_quantity for bp in branch_products if bp.is_available),
        branch_count=len([bp for bp in branch_products if bp.is_available]),
        low_stock_branches=len([
            bp for bp in branch_products
            if bp.is_available and bp.is_low_stock
        ]),
        branch_stocks=[
            BranchStock(
                id=bp.branch_id,
                name=bp.branch_name,
                stock=bp.active_quantity,
                is_available=bp.is_available,
                branch_type=bp.branch_type
            ) for bp in branch_products
            if bp.is_available
        ]
    )

    return ProductAnalytics(stock_analytics=stock_analytics)

def get_start_date(time_range: str) -> datetime:
    end_date = datetime.now()
    if time_range == "7d":
        return end_date - timedelta(days=7)
    elif time_range == "30d":
        return end_date - timedelta(days=30)
    elif time_range == "90d":
        return end_date - timedelta(days=90)
    else:  # 1y
        return end_date - timedelta(days=365)

@router.get("/overview")
async def get_analytics_overview(
    time_range: str,
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
):
    end_date = datetime.now()
    start_date = get_start_date(time_range)

    # Get sales data from inventory reports
    sales_data = db.query(
        InvReport.created_at,
        InvReportItem.offtake,
        InvReportItem.current_srp,
        InvReportItem.current_cost,
        Product.name.label('product_name'),
        Product.id.label('product_id'),
        Branch.id.label('branch_id'),
        Branch.name.label('branch_name')
    ).join(
        Product,
        Product.id == InvReportItem.product_id
    ).join(
        InvReport,
        InvReport.id == InvReportItem.invreport_id
    ).join(
        Branch,
        Branch.id == InvReport.branch_id
    ).filter(
        InvReport.created_at >= start_date,
        InvReport.created_at <= end_date
    ).all()

    # Get expense data
    expense_data = db.query(
        Expense.date_created,
        Expense.amount,
        Expense.type,
        Expense.name,
        Branch.name.label('branch_name')
    ).join(
        Branch,
        Branch.id == Expense.branch_id
    ).filter(
        Expense.date_created >= start_date,
        Expense.date_created <= end_date
    ).all()

    # Calculate metrics
    total_revenue = sum(sale.offtake * sale.current_srp for sale in sales_data)
    total_expenses = sum(expense.amount for expense in expense_data)
    total_cost = sum(sale.offtake * sale.current_cost for sale in sales_data)
    gross_profit = total_revenue - total_cost
    net_profit = gross_profit - total_expenses
    profit_margin = calculate_profit_margin_percentage(gross_profit, total_revenue)

    # Get time series data
    time_series = get_time_series_data(db, start_date, end_date)

    # Calculate branch performance
    branch_performance = {}
    for sale in sales_data:
        if sale.branch_id not in branch_performance:
            branch_performance[sale.branch_id] = {
                "branch_id": sale.branch_id,
                "branch_name": sale.branch_name,
                "total_sales": 0,
                "revenue": 0,
                "total_expenses": 0,
                "profit": 0
            }
        branch_performance[sale.branch_id]["total_sales"] += sale.offtake
        branch_performance[sale.branch_id]["revenue"] += sale.offtake * sale.current_srp
        branch_performance[sale.branch_id]["profit"] += (sale.offtake * sale.current_srp) - (sale.offtake * sale.current_cost)

    # Get top products
    product_performance = {}
    for sale in sales_data:
        if sale.product_id not in product_performance:
            product_performance[sale.product_id] = {
                "id": sale.product_id,
                "name": sale.product_name,
                "total_sales": 0,
                "revenue": 0,
                "total_cost": 0
            }
        product_performance[sale.product_id]["total_sales"] += sale.offtake
        product_performance[sale.product_id]["revenue"] += sale.offtake * sale.current_srp
        product_performance[sale.product_id]["total_cost"] += sale.offtake * sale.current_cost
        product_performance[sale.product_id]["profit_margin"] = calculate_profit_margin_percentage(
            product_performance[sale.product_id]["revenue"] - product_performance[sale.product_id]["total_cost"],
            product_performance[sale.product_id]["revenue"]
        )

    # Get inventory status
    inventory_status = db.query(
        func.count(distinct(Product.id)).label('total_products'),
        func.count(distinct(case([(BranchProduct.is_low_stock == True, Product.id)], else_=None))).label('low_stock_count'),
        func.count(distinct(case([(ProductBatch.quantity == 0, Product.id)], else_=None))).label('out_of_stock_count'),
        func.count(distinct(case([(ProductBatch.expiration_date <= end_date + timedelta(days=30), Product.id)], else_=None))).label('near_expiry_count')
    ).select_from(Product).join(
        BranchProduct
    ).join(
        ProductBatch,
        sa.and_(
            ProductBatch.product_id == Product.id,
            ProductBatch.branch_id == BranchProduct.branch_id,
            ProductBatch.is_active == True
        )
    )

    return {
        "total_revenue": total_revenue,
        "total_sales": sum(sale.offtake for sale in sales_data),
        "total_expenses": total_expenses,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
        "profit_margin": profit_margin,
        "branch_performance": sorted(
            branch_performance.values(),
            key=lambda x: x["revenue"],
            reverse=True
        ),
        "top_products": sorted(
            [
                {
                    **product,
                    "profit_margin": calculate_profit_margin(product)
                }
                for product in product_performance.values()
            ],
            key=lambda x: x["revenue"],
            reverse=True
        ),
        "revenue_trend": time_series["revenue"],
        "expense_trend": time_series["expenses"],
        "profit_trend": time_series["profit"],
        "inventory": {
            "total_products": inventory_status.total_products or 0,
            "low_stock_count": inventory_status.low_stock_count or 0,
            "out_of_stock_count": inventory_status.out_of_stock_count or 0,
            "near_expiry_count": inventory_status.near_expiry_count or 0
        }
    }

