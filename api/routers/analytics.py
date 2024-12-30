from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, desc, and_, case, distinct, select
from datetime import datetime, timedelta, date
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import sqlalchemy as sa
from sqlalchemy.sql import exists

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
    is_low_stock: bool
    low_stock_since: Optional[datetime] = None
    days_in_low_stock: int = 0

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
        .outerjoin(ProductBatch)
        .filter(BranchProduct.is_available == True)
        .group_by(
            BranchProduct.product_id,
            BranchProduct.branch_id,
            Product.id,
            Branch.id,
            BranchProduct.is_available,
            BranchProduct.low_stock_since
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
                "threshold": threshold,
                "low_stock_since": bp.low_stock_since,
                "days_in_low_stock": bp.days_in_low_stock
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
    time_range: str = "30d",
    branch_type: str = "retail"
):
    # Get product details
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Modify branch products query to filter by type
    branch_products = (
        db.query(
            BranchProduct.branch_id,
            Branch.branch_name.label("branch_name"),
            Branch.branch_type.label("branch_type"),
            BranchProduct.is_available,
            BranchProduct.low_stock_since,
            Product.wholesale_low_stock_threshold,
            Product.retail_low_stock_threshold,
            sa.func.coalesce(
                sa.func.sum(
                    sa.case(
                        (
                            sa.and_(
                                ProductBatch.is_active == True,
                                ProductBatch.branch_id == BranchProduct.branch_id
                            ),
                            ProductBatch.quantity
                        ),
                        else_=0
                    )
                ),
                0
            ).label("active_quantity")
        )
        .select_from(BranchProduct)
        .join(Branch, Branch.id == BranchProduct.branch_id)
        .join(Product, Product.id == BranchProduct.product_id)
        .outerjoin(ProductBatch)
        .filter(
            BranchProduct.product_id == product_id,
            BranchProduct.is_available == True,
            Branch.branch_type == branch_type,
            sa.case(
                (Branch.branch_type == 'wholesale', Product.is_wholesale_available),
                else_=Product.is_retail_available
            )
        )
        .group_by(
            BranchProduct.branch_id,
            Branch.branch_name,
            Branch.branch_type,
            BranchProduct.is_available,
            BranchProduct.low_stock_since,
            Product.wholesale_low_stock_threshold,
            Product.retail_low_stock_threshold
        )
        .all()
    )

    # Modify sales data query to filter by branch type
    sales_data = (
        db.query(
            func.sum(InvReportItem.offtake).label('total_quantity'),
            func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('total_revenue'),
            func.sum(InvReportItem.offtake * InvReportItem.current_cost).label('total_cost')
        )
        .join(InvReport)
        .join(Branch)
        .filter(
            InvReportItem.product_id == product_id,
            InvReport.created_at >= get_start_date(time_range),
            InvReport.created_at <= datetime.now(),
            Branch.branch_type == branch_type
        )
        .first()
    )

    # Calculate analytics
    stock_analytics = StockAnalytics(
        total_stock=sum(bp.active_quantity for bp in branch_products),
        branch_count=len(branch_products),
        low_stock_branches=len([
            bp for bp in branch_products
            if bp.active_quantity <= (
                bp.wholesale_low_stock_threshold 
                if bp.branch_type == 'wholesale' 
                else bp.retail_low_stock_threshold
            )
        ]),
        branch_stocks=[
            BranchStock(
                id=bp.branch_id,
                name=bp.branch_name,
                stock=bp.active_quantity,
                is_available=bp.is_available,
                branch_type=bp.branch_type,
                is_low_stock=bp.active_quantity <= (
                    bp.wholesale_low_stock_threshold 
                    if bp.branch_type == 'wholesale' 
                    else bp.retail_low_stock_threshold
                ),
                low_stock_since=bp.low_stock_since,
                days_in_low_stock=(datetime.now() - bp.low_stock_since).days if bp.low_stock_since else 0
            ) for bp in branch_products
        ]
    )

    # Get start date based on time range
    end_date = datetime.now()
    start_date = get_start_date(time_range)

    # Get price history
    price_history = (
        db.query(PriceHistory)
        .filter(
            PriceHistory.product_id == product_id,
            PriceHistory.date >= start_date,
            PriceHistory.date <= end_date
        )
        .order_by(PriceHistory.date.asc())
        .all()
    )

    # Calculate average margin from sales data
    total_revenue = sales_data.total_revenue if sales_data.total_revenue else 0
    total_cost = sales_data.total_cost if sales_data.total_cost else 0
    avg_margin = ((total_revenue - total_cost) / total_revenue * 100) if total_revenue > 0 else 0

    # Get branch-specific sales data
    branch_sales = (
        db.query(
            InvReport.branch_id,
            Branch.branch_name,
            Branch.branch_type,
            func.sum(InvReportItem.offtake).label('total_quantity'),
            func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('total_revenue'),
            func.sum(InvReportItem.offtake * InvReportItem.current_cost).label('total_cost'),
            BranchProduct.is_available,
            Product.is_retail_available,
            Product.is_wholesale_available
        )
        .select_from(InvReportItem)
        .join(InvReport, InvReport.id == InvReportItem.invreport_id)
        .join(Branch, Branch.id == InvReport.branch_id)
        .join(BranchProduct, and_(
            BranchProduct.product_id == InvReportItem.product_id,
            BranchProduct.branch_id == InvReport.branch_id
        ))
        .join(Product, Product.id == InvReportItem.product_id)
        .filter(
            InvReportItem.product_id == product_id,
            InvReport.created_at >= start_date,
            InvReport.created_at <= end_date,
            BranchProduct.is_available == True,
            sa.case(
                (Branch.branch_type == 'wholesale', Product.is_wholesale_available),
                else_=Product.is_retail_available
            )
        )
        .group_by(
            InvReport.branch_id,
            Branch.branch_name,
            Branch.branch_type,
            BranchProduct.is_available,
            Product.is_retail_available,
            Product.is_wholesale_available
        )
        .all()
    )

    # Create branch performance data
    branch_performance = [
        {
            "branch_id": sale.branch_id,
            "branch_name": sale.branch_name,
            "branch_type": sale.branch_type,
            "quantity": int(sale.total_quantity),
            "revenue": float(sale.total_revenue) if sale.total_revenue else 0,
            "cost": float(sale.total_cost) if sale.total_cost else 0,
            "gross_profit": float(sale.total_revenue - sale.total_cost) if sale.total_revenue and sale.total_cost else 0,
            "profit_margin": float((sale.total_revenue - sale.total_cost) / sale.total_revenue * 100) 
                if sale.total_revenue and sale.total_cost and sale.total_revenue > 0 else 0
        }
        for sale in branch_sales
    ]

    return ProductAnalytics(
        stock_analytics=stock_analytics,
        total_sales={
            "quantity": int(sales_data.total_quantity or 0),
            "revenue": float(total_revenue)
        },
        current_price={
            "cost": float(product.cost),
            "srp": float(product.srp)
        },
        price_history=[{
            "date": ph.date,
            "cost": float(ph.cost),
            "srp": float(ph.srp),
            "margin": ((ph.srp - ph.cost) / ph.srp * 100) if ph.srp > 0 else 0
        } for ph in price_history],
        branch_performance=branch_performance,
        price_analytics={
            "avg_margin": float(avg_margin),
            "change_count": len(price_history)
        }
    )

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
async def get_company_overview(
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN]))],
    time_range: str = "30d",
    branch_type: str = "retail"
):
    end_date = datetime.now()
    start_date = get_start_date(time_range)
    
    # Get branches of specified type
    branches = db.query(Branch).filter(
        Branch.is_active == True,
        Branch.branch_type == branch_type
    ).all()
    branch_ids = [b.id for b in branches]

    # Calculate overall metrics (existing code)
    sales_data = db.query(
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('total_revenue'),
        func.sum(InvReportItem.offtake).label('total_sales'),
        func.sum(InvReportItem.offtake * (InvReportItem.current_srp - InvReportItem.current_cost)).label('gross_profit')
    ).join(
        InvReport,
        InvReport.id == InvReportItem.invreport_id
    ).filter(
        InvReport.branch_id.in_(branch_ids),
        InvReport.created_at.between(start_date, end_date)
    ).first()
    
    # Get branch performance
    branch_performance = db.query(
        Branch.id.label('branch_id'),
        Branch.branch_name,
        func.sum(InvReportItem.offtake).label('total_sales'),
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('revenue'),
        func.sum(Expense.amount).label('total_expenses')
    ).join(
        InvReport, InvReport.branch_id == Branch.id
    ).join(
        InvReportItem, InvReportItem.invreport_id == InvReport.id
    ).outerjoin(
        Expense, and_(
            Expense.branch_id == Branch.id,
            Expense.date_created.between(start_date, end_date)
        )
    ).filter(
        Branch.id.in_(branch_ids),
        InvReport.created_at.between(start_date, end_date)
    ).group_by(
        Branch.id,
        Branch.branch_name
    ).all()

    # Get top products
    top_products = db.query(
        Product.id,
        Product.name,
        func.sum(InvReportItem.offtake).label('total_sales'),
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).label('revenue'),
        func.sum(InvReportItem.offtake * (InvReportItem.current_srp - InvReportItem.current_cost)).label('profit')
    ).join(
        InvReportItem, InvReportItem.product_id == Product.id
    ).join(
        InvReport, and_(
            InvReport.id == InvReportItem.invreport_id,
            InvReport.branch_id.in_(branch_ids),
            InvReport.created_at.between(start_date, end_date)
        )
    ).group_by(
        Product.id,
        Product.name
    ).order_by(
        func.sum(InvReportItem.offtake * InvReportItem.current_srp).desc()
    ).limit(5).all()

    # Rest of the existing code
    total_revenue = float(sales_data.total_revenue or 0)
    gross_profit = float(sales_data.gross_profit or 0)
    total_expenses = db.query(func.sum(Expense.amount)).filter(
        Expense.branch_id.in_(branch_ids),
        Expense.date_created.between(start_date, end_date)
    ).scalar() or 0
    net_profit = gross_profit - total_expenses
    profit_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0

    # Calculate revenue trend.
    revenue_trend = db.query(
        func.date(AnalyticsTimeSeries.timestamp).label('date'),
        func.sum(AnalyticsTimeSeries.value).label('value'),
        func.sum(AnalyticsTimeSeries.value).label('gross_value'),
        func.coalesce(func.sum(Expense.amount), 0).label('expenses')
    ).outerjoin(
        Expense,
        and_(
            func.date(Expense.date_created) == func.date(AnalyticsTimeSeries.timestamp),
            Expense.branch_id.in_(branch_ids)
        )
    ).filter(
        AnalyticsTimeSeries.branch_id.in_(branch_ids),
        AnalyticsTimeSeries.timestamp.between(start_date, end_date)
    ).group_by(
        func.date(AnalyticsTimeSeries.timestamp)
    ).order_by(
        func.date(AnalyticsTimeSeries.timestamp)
    ).all()

    # First, get the total quantity per branch and product
    product_quantities = db.query(
        Branch.id.label('branch_id'),
        Branch.branch_type,
        Product.id.label('product_id'),
        Product.wholesale_low_stock_threshold,
        Product.retail_low_stock_threshold,
        func.sum(case((ProductBatch.is_active == True, ProductBatch.quantity), else_=0)).label('total_quantity')
    ).join(
        BranchProduct, BranchProduct.branch_id == Branch.id
    ).join(
        Product, Product.id == BranchProduct.product_id
    ).outerjoin(
        ProductBatch, and_(
            ProductBatch.product_id == Product.id,
            ProductBatch.branch_id == Branch.id
        )
    ).filter(
        Branch.id.in_(branch_ids)
    ).group_by(
        Branch.id,
        Branch.branch_type,
        Product.id,
        Product.wholesale_low_stock_threshold,
        Product.retail_low_stock_threshold
    ).subquery()

    # Then use this subquery for the branch-level counts
    inventory_stats = db.query(
        func.count(distinct(Branch.id)).label('total_branches'),
        func.count(distinct(case(
            (exists(
                select(1).select_from(product_quantities)
                .join(BranchProduct, and_(
                    BranchProduct.branch_id == product_quantities.c.branch_id,
                    BranchProduct.product_id == product_quantities.c.product_id,
                    BranchProduct.is_available == True
                ))
                .correlate(Branch)
                .where(and_(
                    product_quantities.c.branch_id == Branch.id,
                    product_quantities.c.total_quantity <= 
                    case(
                        (product_quantities.c.branch_type == 'wholesale', 
                         product_quantities.c.wholesale_low_stock_threshold),
                        else_=product_quantities.c.retail_low_stock_threshold
                    )
                ))
            ), Branch.id)
        ))).label('low_stock_branches'),
        func.count(distinct(case(
            (exists(
                select(1).select_from(ProductBatch)
                .correlate(Branch)
                .where(and_(
                    ProductBatch.branch_id == Branch.id,
                    ProductBatch.expiration_date <= datetime.now() + timedelta(days=30),
                    ProductBatch.is_active == True,
                    ProductBatch.quantity > 0
                ))
            ), Branch.id)
        ))).label('near_expiry_branches')
    ).select_from(Branch).filter(
        Branch.id.in_(branch_ids)
    ).first()

    return {
        "total_revenue": total_revenue,
        "total_sales": int(sales_data.total_sales or 0),
        "total_expenses": float(total_expenses),
        "gross_profit": gross_profit,
        "net_profit": net_profit,
        "profit_margin": profit_margin,
        "active_branches": len(branches),
        "branch_performance": [{
            "branch_id": bp.branch_id,
            "branch_name": bp.branch_name,
            "total_sales": int(bp.total_sales or 0),
            "revenue": float(bp.revenue or 0),
            "total_expenses": float(bp.total_expenses or 0),
            "profit": float((bp.revenue or 0) - (bp.total_expenses or 0))
        } for bp in branch_performance],
        "top_products": [{
            "id": p.id,
            "name": p.name,
            "total_sales": int(p.total_sales or 0),
            "revenue": float(p.revenue or 0),
            "profit_margin": float(p.profit / p.revenue * 100) if p.revenue else 0
        } for p in top_products],
        "revenue_trend": [{
            "timestamp": entry.date.isoformat(),
            "value": float(entry.value),
            "profit": float(entry.gross_value - entry.expenses),
            "expenses": float(entry.expenses)
        } for entry in revenue_trend],
        "inventory": {
            "total_branches": int(inventory_stats.total_branches or 0),
            "low_stock_branches": int(inventory_stats.low_stock_branches or 0),
            "near_expiry_branches": int(inventory_stats.near_expiry_branches or 0)
        }
    }

@router.get("/monthly-comparison")
async def get_monthly_comparison(
    db: db_dependency,
    current_user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
    branch_type: str = "retail",
    branch_id: Optional[int] = None
):
    # Get current and previous month dates
    today = datetime.now()
    current_month_start = datetime(today.year, today.month, 1)
    previous_month_start = (current_month_start - timedelta(days=1)).replace(day=1)
    two_months_ago_start = (previous_month_start - timedelta(days=1)).replace(day=1)
    
    # Build branch filter
    if branch_id and current_user["role"] == UserRole.PHARMACIST or current_user["role"] == UserRole.WHOLESALER:
        if branch_id != current_user["branch_id"]:
            raise HTTPException(status_code=403, detail="Not authorized to view this branch's data")
        branch_ids = [branch_id]
    elif branch_id:
        branch_ids = [branch_id]
    else:
        # Get branches of specified type
        branches = db.query(Branch).filter(
            Branch.is_active == True,
            Branch.branch_type == branch_type
        ).all()
        branch_ids = [b.id for b in branches]

    # Get previous month revenue
    prev_month_revenue = db.query(
        func.sum(InvReportItem.offtake * InvReportItem.current_srp)
    ).join(
        InvReport,
        InvReport.id == InvReportItem.invreport_id
    ).filter(
        InvReport.branch_id.in_(branch_ids),
        InvReport.created_at >= previous_month_start,
        InvReport.created_at < current_month_start
    ).scalar() or 0

    # Get two months ago revenue
    two_months_ago_revenue = db.query(
        func.sum(InvReportItem.offtake * InvReportItem.current_srp)
    ).join(
        InvReport,
        InvReport.id == InvReportItem.invreport_id
    ).filter(
        InvReport.branch_id.in_(branch_ids),
        InvReport.created_at >= two_months_ago_start,
        InvReport.created_at < previous_month_start
    ).scalar() or 0

    # Get previous month expenses
    prev_month_expenses = db.query(
        func.sum(Expense.amount)
    ).filter(
        Expense.branch_id.in_(branch_ids),
        Expense.date_created >= previous_month_start,
        Expense.date_created < current_month_start
    ).scalar() or 0

    # Get two months ago expenses
    two_months_ago_expenses = db.query(
        func.sum(Expense.amount)
    ).filter(
        Expense.branch_id.in_(branch_ids),
        Expense.date_created >= two_months_ago_start,
        Expense.date_created < previous_month_start
    ).scalar() or 0

    # Calculate percentage changes
    revenue_change = ((prev_month_revenue - two_months_ago_revenue) / two_months_ago_revenue * 100) if two_months_ago_revenue > 0 else 0
    expense_change = ((prev_month_expenses - two_months_ago_expenses) / two_months_ago_expenses * 100) if two_months_ago_expenses > 0 else 0

    return {
        "previous_month": {
            "revenue": float(prev_month_revenue),
            "revenue_change": float(revenue_change),
            "expenses": float(prev_month_expenses),
            "expense_change": float(expense_change)
        },
        "month": previous_month_start.strftime("%B %Y"),
        "branch_id": branch_id
    }

