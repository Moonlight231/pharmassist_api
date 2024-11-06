from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Annotated
from pydantic import BaseModel, computed_field
from datetime import date, datetime, timedelta

from api.models import InvReport, InvReportItem, BranchProduct, Product, UserRole, ProductBatch
from api.deps import db_dependency, role_required

router = APIRouter(
    prefix='/inventory-reports',
    tags=['inventory reports']
)

class BatchDeliveryInfo(BaseModel):
    lot_number: str
    quantity: int
    expiration_date: date

class InvReportItemBase(BaseModel):
    product_id: int
    beginning: int
    deliver: int
    transfer: int
    pull_out: int
    offtake: int
    selling_area: int
    delivery_batches: Optional[List[BatchDeliveryInfo]] = None

class InvReportCreate(BaseModel):
    branch_id: int
    start_date: date
    end_date: date
    items: List[InvReportItemBase]

class InvReportItemResponse(BaseModel):
    id: int
    product_id: int
    beginning: int
    deliver: int
    transfer: int
    pull_out: int
    offtake: int
    selling_area: int
    current_cost: float
    current_srp: float
    peso_value: float
    delivery_batches: Optional[List[BatchDeliveryInfo]] = None

    class Config:
        from_attributes = True

class InvReportResponse(BaseModel):
    id: int
    branch_id: int
    created_at: datetime
    start_date: date
    end_date: date
    items: List[InvReportItemResponse]

    class Config:
        from_attributes = True

    @computed_field
    def items_count(self) -> int:
        return len(self.items)

def update_batch_quantities(db: Session, branch_id: int, product_id: int, used_quantity: int):
    remaining = used_quantity
    batches = (db.query(ProductBatch)
        .filter(
            ProductBatch.branch_id == branch_id,
            ProductBatch.product_id == product_id,
            ProductBatch.is_active == True
        )
        .order_by(ProductBatch.expiration_date)
        .all())

    for batch in batches:
        if batch.quantity <= remaining:
            remaining -= batch.quantity
            batch.is_active = False
        else:
            batch.quantity -= remaining
            break

    if remaining > 0:
        raise HTTPException(
            status_code=400,
            detail="Insufficient quantity available"
        )

@router.post('/', response_model=InvReportResponse, status_code=status.HTTP_201_CREATED)
def create_inventory_report(
    report: InvReportCreate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required(UserRole.PHARMACIST))]
):
    current_time = datetime.now()
    new_report = InvReport(
        branch_id=report.branch_id,
        created_at=current_time,
        start_date=report.start_date,
        end_date=report.end_date
    )
    db.add(new_report)
    db.flush()

    for item in report.items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if not product:
            raise HTTPException(
                status_code=404,
                detail=f"Product with id {item.product_id} not found"
            )

        # Exclude delivery_batches from the dictionary
        item_dict = item.dict(exclude={'delivery_batches'})
        
        report_item = InvReportItem(
            **item_dict,
            invreport_id=new_report.id,
            current_cost=product.cost,
            current_srp=product.srp
        )
        db.add(report_item)

        # Update branch products directly
        branch_product = db.query(BranchProduct).filter(
            BranchProduct.branch_id == report.branch_id,
            BranchProduct.product_id == item.product_id
        ).first()

        if branch_product:
            branch_product.quantity = item.selling_area
        else:
            new_branch_product = BranchProduct(
                branch_id=report.branch_id,
                product_id=item.product_id,
                quantity=item.selling_area
            )
            db.add(new_branch_product)

        if item.deliver > 0 and item.delivery_batches:
            total_delivered = sum(batch.quantity for batch in item.delivery_batches)
            if total_delivered != item.deliver:
                raise HTTPException(
                    status_code=400,
                    detail="Sum of batch quantities must equal total delivery amount"
                )
            
            for batch_info in item.delivery_batches:
                existing_batch = db.query(ProductBatch).filter(
                    ProductBatch.branch_id == report.branch_id,
                    ProductBatch.product_id == item.product_id,
                    ProductBatch.lot_number == batch_info.lot_number,
                    ProductBatch.is_active == True
                ).first()

                if existing_batch:
                    if existing_batch.expiration_date != batch_info.expiration_date:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Expiration date mismatch for lot number {batch_info.lot_number}"
                        )
                    existing_batch.quantity += batch_info.quantity
                else:
                    new_batch = ProductBatch(
                        branch_id=report.branch_id,
                        product_id=item.product_id,
                        lot_number=batch_info.lot_number,
                        quantity=batch_info.quantity,
                        expiration_date=batch_info.expiration_date
                    )
                    db.add(new_batch)

        total_usage = item.offtake + item.transfer + item.pull_out
        if total_usage > 0:
            update_batch_quantities(db, report.branch_id, item.product_id, total_usage)

    db.commit()
    db.refresh(new_report)
    return new_report

@router.get('/{report_id}', response_model=InvReportResponse)
def get_inventory_report(
    report_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))]
):
    report = db.query(InvReport).options(joinedload(InvReport.items)).filter(InvReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Inventory report not found")
    
    # Get batches created during this report
    for item in report.items:
        batches = db.query(ProductBatch).filter(
            ProductBatch.branch_id == report.branch_id,
            ProductBatch.product_id == item.product_id,
            ProductBatch.created_at >= report.created_at,
            ProductBatch.created_at <= report.created_at + timedelta(minutes=1)
        ).all()
        
        item.delivery_batches = [
            BatchDeliveryInfo(
                lot_number=batch.lot_number,
                quantity=batch.quantity,
                expiration_date=batch.expiration_date
            )
            for batch in batches
        ]

    return report

from typing import List, Optional

@router.get('/', response_model=List[InvReportResponse])
def get_all_inventory_reports(
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
    skip: int = 0,
    limit: int = 100
):
    reports = (db.query(InvReport)
        .options(joinedload(InvReport.items))
        .offset(skip)
        .limit(limit)
        .all())
    
    # Add delivery batches info
    for report in reports:
        for item in report.items:
            batches = db.query(ProductBatch).filter(
                ProductBatch.branch_id == report.branch_id,
                ProductBatch.product_id == item.product_id,
                ProductBatch.created_at >= report.created_at,
                ProductBatch.created_at <= report.created_at + timedelta(minutes=1)
            ).all()
            
            item.delivery_batches = [
                BatchDeliveryInfo(
                    lot_number=batch.lot_number,
                    quantity=batch.quantity,
                    expiration_date=batch.expiration_date
                )
                for batch in batches
            ]
    
    return reports

@router.get('/branch/{branch_id}', response_model=List[InvReportResponse])
def get_branch_inventory_reports(
    branch_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
    skip: int = 0,
    limit: int = 100
):
    reports = db.query(InvReport).filter(InvReport.branch_id == branch_id).offset(skip).limit(limit).all()
    return reports

class ExpiringBatchItem(BaseModel):
    product_id: int
    lot_number: str
    quantity: int
    expiration_date: str
    days_until_expiry: int

class ProductBatchItem(BaseModel):
    lot_number: str
    quantity: int
    expiration_date: str
    days_until_expiry: int
    status: str

class ProductBatchSummary(BaseModel):
    total_quantity: int
    expired: int
    critical: int
    warning: int

class ProductBatchesResponse(BaseModel):
    product_id: int
    batches: List[ProductBatchItem]
    summary: ProductBatchSummary

class BranchExpiringBatchesResponse(BaseModel):
    expired: List[ExpiringBatchItem]
    critical: List[ExpiringBatchItem]
    warning: List[ExpiringBatchItem]

@router.get('/expiring-batches/{branch_id}', response_model=BranchExpiringBatchesResponse)
def get_branch_expiring_batches(
    branch_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required(UserRole.PHARMACIST))]
):
    """Get all batch suggestions for a branch, grouped by expiry status"""
    batches = (db.query(ProductBatch)
        .filter(
            ProductBatch.branch_id == branch_id,
            ProductBatch.is_active == True
        )
        .order_by(ProductBatch.expiration_date)
        .all())

    return {
        "expired": [
            {
                "product_id": batch.product_id,
                "lot_number": batch.lot_number,
                "quantity": batch.quantity,
                "expiration_date": batch.expiration_date.isoformat(),
                "days_until_expiry": batch.days_until_expiry
            }
            for batch in batches if batch.expiry_status == "expired"
        ],
        "critical": [
            {
                "product_id": batch.product_id,
                "lot_number": batch.lot_number,
                "quantity": batch.quantity,
                "expiration_date": batch.expiration_date.isoformat(),
                "days_until_expiry": batch.days_until_expiry
            }
            for batch in batches if batch.expiry_status == "critical"
        ],
        "warning": [
            {
                "product_id": batch.product_id,
                "lot_number": batch.lot_number,
                "quantity": batch.quantity,
                "expiration_date": batch.expiration_date.isoformat(),
                "days_until_expiry": batch.days_until_expiry
            }
            for batch in batches if batch.expiry_status == "warning"
        ]
    }

@router.get('/product-batches/{branch_id}/{product_id}', response_model=ProductBatchesResponse)
def get_product_batches(
    branch_id: int,
    product_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required(UserRole.PHARMACIST))]
):
    """Get batch suggestions for a specific product in a branch"""
    batches = (db.query(ProductBatch)
        .filter(
            ProductBatch.branch_id == branch_id,
            ProductBatch.product_id == product_id,
            ProductBatch.is_active == True
        )
        .order_by(ProductBatch.expiration_date)
        .all())

    return {
        "product_id": product_id,
        "batches": [
            {
                "lot_number": batch.lot_number,
                "quantity": batch.quantity,
                "expiration_date": batch.expiration_date.isoformat(),
                "days_until_expiry": batch.days_until_expiry,
                "status": batch.expiry_status,
            }
            for batch in batches
        ],
        "summary": {
            "total_quantity": sum(batch.quantity for batch in batches),
            "expired": sum(1 for batch in batches if batch.expiry_status == "expired"),
            "critical": sum(1 for batch in batches if batch.expiry_status == "critical"),
            "warning": sum(1 for batch in batches if batch.expiry_status == "warning")
        }
    }