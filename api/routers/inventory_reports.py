from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
import sqlalchemy as sa
from typing import List, Optional, Annotated
from pydantic import BaseModel, computed_field
from datetime import date, datetime, timedelta

from api.models import InvReport, InvReportItem, BranchProduct, Product, UserRole, ProductBatch, InvReportBatch
from api.deps import db_dependency, role_required

router = APIRouter(
    prefix='/inventory-reports',
    tags=['inventory reports']
)

class BatchDeliveryInfo(BaseModel):
    lot_number: str
    quantity: int
    expiration_date: date

class BatchTransferInfo(BaseModel):
    lot_number: str
    quantity: int
    expiration_date: date

class PullOutBatchInfo(BaseModel):
    lot_number: str
    quantity: int

class InvReportItemBase(BaseModel):
    product_id: int
    beginning: int
    offtake: int
    selling_area: int
    pull_out_batches: Optional[List[PullOutBatchInfo]] = None
    delivery_batches: Optional[List[BatchDeliveryInfo]] = None
    transfer_batches: Optional[List[BatchTransferInfo]] = None

    @computed_field
    def pull_out(self) -> int:
        if not self.pull_out_batches:
            return 0
        return sum(batch.quantity for batch in self.pull_out_batches)

    @computed_field
    def deliver(self) -> int:
        if not self.delivery_batches:
            return 0
        return sum(batch.quantity for batch in self.delivery_batches)

    @computed_field
    def transfer(self) -> int:
        if not self.transfer_batches:
            return 0
        return sum(batch.quantity for batch in self.transfer_batches)

class InvReportCreate(BaseModel):
    branch_id: int
    start_date: date
    end_date: date
    items: List[InvReportItemBase]

class BatchInfo(BaseModel):
    lot_number: str
    quantity: int
    expiration_date: date
    batch_type: str

    class Config:
        from_attributes = True

class InvReportItemResponse(BaseModel):
    id: int
    product_id: int
    beginning: int
    offtake: int
    selling_area: int
    current_cost: float
    current_srp: float
    batches: List[BatchInfo]

    @computed_field
    def deliver(self) -> int:
        return sum(b.quantity for b in self.batches if b.batch_type == 'delivery')

    @computed_field
    def transfer(self) -> int:
        return sum(b.quantity for b in self.batches if b.batch_type == 'transfer')

    @computed_field
    def pull_out(self) -> int:
        return sum(b.quantity for b in self.batches if b.batch_type == 'pull_out')

    @computed_field
    def peso_value(self) -> float:
        return self.selling_area * self.current_srp

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
    
    # Get all active batches including newly added ones
    batches = (db.query(ProductBatch)
        .filter(
            ProductBatch.branch_id == branch_id,
            ProductBatch.product_id == product_id,
            ProductBatch.is_active == True
        )
        .order_by(ProductBatch.expiration_date)
        .all())

    # Get pending batches from the current transaction
    pending_batches = [
        obj for obj in db.new 
        if isinstance(obj, ProductBatch) 
        and obj.branch_id == branch_id 
        and obj.product_id == product_id
    ]
    
    # Combine and sort all batches by expiration date
    all_batches = sorted(batches + pending_batches, key=lambda x: x.expiration_date)
    
    # Calculate total available quantity
    total_available = sum(batch.quantity for batch in all_batches)
    
    if total_available < used_quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient quantity available. Required: {used_quantity}, Available: {total_available}"
        )

    # Process all batches in order of expiration date
    for batch in all_batches:
        if batch.quantity <= remaining:
            remaining -= batch.quantity
            batch.is_active = False
        else:
            batch.quantity -= remaining
            break

def process_batch(db: Session, branch_id: int, product_id: int, batch_info: BatchDeliveryInfo | BatchTransferInfo, current_time: datetime):
    """Process a single batch delivery or transfer"""
    # First save to InvReportBatch - this preserves the original entry
    new_report_batch = InvReportBatch(
        lot_number=batch_info.lot_number,
        quantity=batch_info.quantity,
        expiration_date=batch_info.expiration_date,
        batch_type='delivery' if isinstance(batch_info, BatchDeliveryInfo) else 'transfer',
        created_at=current_time
    )
    db.add(new_report_batch)

    # Then handle ProductBatch - merge quantities for same lot number
    existing_batch = db.query(ProductBatch).filter(
        ProductBatch.branch_id == branch_id,
        ProductBatch.product_id == product_id,
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
            branch_id=branch_id,
            product_id=product_id,
            lot_number=batch_info.lot_number,
            quantity=batch_info.quantity,
            expiration_date=batch_info.expiration_date,
            created_at=current_time
        )
        db.add(new_batch)

def update_branch_product_quantity(db: Session, branch_id: int, product_id: int):
    """Update branch product quantity to match sum of active batch quantities"""
    total_quantity = db.query(sa.func.sum(ProductBatch.quantity))\
        .filter(
            ProductBatch.branch_id == branch_id,
            ProductBatch.product_id == product_id,
            ProductBatch.is_active == True
        ).scalar() or 0
    
    branch_product = db.query(BranchProduct)\
        .filter(
            BranchProduct.branch_id == branch_id,
            BranchProduct.product_id == product_id
        ).first()
    
    if branch_product:
        branch_product.quantity = total_quantity

@router.post('/', response_model=InvReportResponse, status_code=status.HTTP_201_CREATED)
def create_inventory_report(
    report: InvReportCreate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required(UserRole.PHARMACIST))]
):
    current_time = datetime.now()
    
    # Create report
    new_report = InvReport(
        branch_id=report.branch_id,
        created_at=current_time,
        start_date=report.start_date,
        end_date=report.end_date
    )
    db.add(new_report)
    
    # Process each item
    for item_data in report.items:
        # Get product info
        product = db.query(Product).get(item_data.product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {item_data.product_id} not found")
        
        # Create report item
        report_item = InvReportItem(
            product_id=item_data.product_id,
            beginning=item_data.beginning,
            offtake=item_data.offtake,
            selling_area=item_data.selling_area,
            current_cost=product.cost,
            current_srp=product.srp
        )
        new_report.items.append(report_item)
        
        # Process delivery batches
        if item_data.delivery_batches:
            for batch in item_data.delivery_batches:
                # Save to InvReportBatch
                report_batch = InvReportBatch(
                    lot_number=batch.lot_number,
                    quantity=batch.quantity,
                    expiration_date=batch.expiration_date,
                    batch_type='delivery',
                    created_at=current_time
                )
                report_item.batches.append(report_batch)
                
                # Update ProductBatch
                existing_batch = db.query(ProductBatch).filter(
                    ProductBatch.branch_id == report.branch_id,
                    ProductBatch.product_id == item_data.product_id,
                    ProductBatch.lot_number == batch.lot_number,
                    ProductBatch.is_active == True
                ).first()
                
                if existing_batch:
                    if existing_batch.expiration_date != batch.expiration_date:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Expiration date mismatch for lot number {batch.lot_number}"
                        )
                    existing_batch.quantity += batch.quantity
                else:
                    new_batch = ProductBatch(
                        branch_id=report.branch_id,
                        product_id=item_data.product_id,
                        lot_number=batch.lot_number,
                        quantity=batch.quantity,
                        expiration_date=batch.expiration_date,
                        created_at=current_time
                    )
                    db.add(new_batch)
        
        # Process transfer batches - similar logic
        if item_data.transfer_batches:
            for batch in item_data.transfer_batches:
                # Save to InvReportBatch
                report_batch = InvReportBatch(
                    lot_number=batch.lot_number,
                    quantity=batch.quantity,
                    expiration_date=batch.expiration_date,
                    batch_type='transfer',
                    created_at=current_time
                )
                report_item.batches.append(report_batch)
                
                # Update ProductBatch
                existing_batch = db.query(ProductBatch).filter(
                    ProductBatch.branch_id == report.branch_id,
                    ProductBatch.product_id == item_data.product_id,
                    ProductBatch.lot_number == batch.lot_number,
                    ProductBatch.is_active == True
                ).first()
                
                if existing_batch:
                    if existing_batch.expiration_date != batch.expiration_date:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Expiration date mismatch for lot number {batch.lot_number}"
                        )
                    existing_batch.quantity += batch.quantity
                else:
                    new_batch = ProductBatch(
                        branch_id=report.branch_id,
                        product_id=item_data.product_id,
                        lot_number=batch.lot_number,
                        quantity=batch.quantity,
                        expiration_date=batch.expiration_date,
                        created_at=current_time
                    )
                    db.add(new_batch)
        
        # Process pull-out batches
        if item_data.pull_out_batches:
            for batch in item_data.pull_out_batches:
                # Get existing batch to get its expiration date
                existing_batch = db.query(ProductBatch).filter(
                    ProductBatch.branch_id == report.branch_id,
                    ProductBatch.product_id == item_data.product_id,
                    ProductBatch.lot_number == batch.lot_number,
                    ProductBatch.is_active == True
                ).first()
                
                if not existing_batch:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Batch with lot number {batch.lot_number} not found"
                    )
                
                if existing_batch.quantity < batch.quantity:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Insufficient quantity in batch {batch.lot_number}"
                    )
                
                # Save to InvReportBatch with expiration date from existing batch
                report_batch = InvReportBatch(
                    lot_number=batch.lot_number,
                    quantity=batch.quantity,
                    expiration_date=existing_batch.expiration_date,
                    batch_type='pull_out',
                    created_at=current_time
                )
                report_item.batches.append(report_batch)
                
                # Update existing batch quantity
                existing_batch.quantity -= batch.quantity
    
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    # Fetch complete report
    complete_report = (
        db.query(InvReport)
        .options(joinedload(InvReport.items).joinedload(InvReportItem.batches))
        .filter(InvReport.id == new_report.id)
        .first()
    )
    
    return complete_report

@router.get('/{report_id}', response_model=InvReportResponse)
def get_inventory_report(
    report_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))]
):
    report = (
        db.query(InvReport)
        .options(
            joinedload(InvReport.items)
            .joinedload(InvReportItem.batches)
        )
        .filter(InvReport.id == report_id)
        .first()
    )
    
    if not report:
        raise HTTPException(status_code=404, detail="Inventory report not found")
    
    return report

@router.get('/', response_model=List[InvReportResponse])
def get_all_inventory_reports(
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
    skip: int = 0,
    limit: int = 100
):
    reports = (
        db.query(InvReport)
        .options(
            joinedload(InvReport.items)
            .joinedload(InvReportItem.batches)
        )
        .order_by(InvReport.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    
    return reports

@router.get('/branch/{branch_id}', response_model=List[InvReportResponse])
def get_branch_inventory_reports(
    branch_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST]))],
    skip: int = 0,
    limit: int = 100
):
    reports = (
        db.query(InvReport)
        .options(
            joinedload(InvReport.items)
            .joinedload(InvReportItem.batches)
        )
        .filter(InvReport.branch_id == branch_id)
        .order_by(InvReport.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    
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

    # Group batches by product_id, lot_number, and expiration_date
    batch_groups = {}
    for batch in batches:
        key = (batch.product_id, batch.lot_number, batch.expiration_date)
        if key not in batch_groups:
            batch_groups[key] = {
                "product_id": batch.product_id,
                "lot_number": batch.lot_number,
                "quantity": batch.quantity,
                "expiration_date": batch.expiration_date.isoformat(),
                "days_until_expiry": batch.days_until_expiry,
                "status": batch.expiry_status
            }
        else:
            batch_groups[key]["quantity"] += batch.quantity

    combined_batches = list(batch_groups.values())

    return {
        "expired": [b for b in combined_batches if b["status"] == "expired"],
        "critical": [b for b in combined_batches if b["status"] == "critical"],
        "warning": [b for b in combined_batches if b["status"] == "warning"]
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

    # Group batches by lot number and expiration date
    batch_groups = {}
    for batch in batches:
        key = (batch.lot_number, batch.expiration_date)
        if key not in batch_groups:
            batch_groups[key] = {
                "lot_number": batch.lot_number,
                "quantity": batch.quantity,
                "expiration_date": batch.expiration_date.isoformat(),
                "days_until_expiry": batch.days_until_expiry,
                "status": batch.expiry_status,
            }
        else:
            batch_groups[key]["quantity"] += batch.quantity

    combined_batches = list(batch_groups.values())
    combined_batches.sort(key=lambda x: x["expiration_date"])

    return {
        "product_id": product_id,
        "batches": combined_batches,
        "summary": {
            "total_quantity": sum(b["quantity"] for b in combined_batches),
            "expired": sum(1 for b in combined_batches if b["status"] == "expired"),
            "critical": sum(1 for b in combined_batches if b["status"] == "critical"),
            "warning": sum(1 for b in combined_batches if b["status"] == "warning")
        }
    }