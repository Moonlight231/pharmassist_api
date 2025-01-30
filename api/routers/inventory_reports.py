from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload, selectinload
import sqlalchemy as sa
from typing import List, Optional, Annotated
from pydantic import BaseModel, computed_field
from datetime import date, datetime, timedelta

from api.models import Branch, InvReport, InvReportItem, BranchProduct, Product, UserRole, ProductBatch, InvReportBatch, AnalyticsTimeSeries
from api.deps import db_dependency, role_required

router = APIRouter(
    prefix='/inventory-reports',
    tags=['inventory reports']
)

class BatchDeliveryInfo(BaseModel):
    quantity: int
    expiration_date: date

class BatchTransferInfo(BaseModel):
    quantity: int
    expiration_date: date

class PullOutBatchInfo(BaseModel):
    quantity: int
    expiration_date: date

class ExpiringBatchItem(BaseModel):
    product_id: int
    quantity: int
    expiration_date: str
    days_until_expiry: int

class ProductBatchItem(BaseModel):
    quantity: int
    expiration_date: str
    days_until_expiry: int
    status: str

class BatchInfo(BaseModel):
    quantity: int
    expiration_date: date
    batch_type: str

    class Config:
        from_attributes = True

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
    start_date: datetime
    end_date: datetime
    items: List[InvReportItemBase]

class ProductResponse(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True

class InvReportItemResponse(BaseModel):
    id: int
    product_id: int
    product: ProductResponse
    beginning: int
    offtake: int
    selling_area: int
    current_cost: float
    current_srp: float
    batches: List[BatchInfo]

    @computed_field
    def product_name(self) -> str:
        return self.product.name if self.product else "Unknown Product"

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

    class Config:
        from_attributes = True

class BranchResponse(BaseModel):
    id: int
    branch_name: str
    location: str
    branch_type: str
    is_active: bool

    class Config:
        from_attributes = True

class InvReportResponse(BaseModel):
    id: int
    branch_id: int
    created_at: datetime
    start_date: datetime
    end_date: datetime
    items: List[InvReportItemResponse]
    branch: Optional[BranchResponse]
    viewed_by: Optional[int] = None

    @computed_field
    def items_count(self) -> int:
        return len(self.items)

    @computed_field
    def branch_name(self) -> str:
        return self.branch.branch_name if self.branch else "Unknown Branch"

    @computed_field
    def is_viewed(self) -> bool:
        return self.viewed_by is not None

    class Config:
        from_attributes = True

class InvReportSummaryResponse(BaseModel):
    id: int
    branch_id: int
    created_at: datetime
    start_date: datetime
    end_date: datetime
    branch: Optional[BranchResponse]
    viewed_by: Optional[int] = None
    products_with_delivery: int
    products_with_transfer: int
    products_with_pullout: int
    products_with_offtake: int
    items_count: int

    @computed_field
    def branch_name(self) -> str:
        return self.branch.branch_name if self.branch else "Unknown Branch"

    @computed_field
    def is_viewed(self) -> bool:
        return self.viewed_by is not None

    class Config:
        from_attributes = True

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
    # Get branch and product first
    branch_product = db.query(BranchProduct)\
        .join(Branch)\
        .join(Product)\
        .filter(
            BranchProduct.branch_id == branch_id,
            BranchProduct.product_id == product_id
        ).first()
    
    if not branch_product:
        raise HTTPException(status_code=404, detail="Branch product not found")
        
    # Check if product is available for this branch type
    if (branch_product.branch.branch_type == 'wholesale' and 
        not branch_product.product.is_wholesale_available):
        raise HTTPException(
            status_code=400,
            detail="This product is not available for wholesale branches"
        )
    elif (branch_product.branch.branch_type == 'retail' and 
          not branch_product.product.is_retail_available):
        raise HTTPException(
            status_code=400,
            detail="This product is not available for retail branches"
        )

    # First save to InvReportBatch
    new_report_batch = InvReportBatch(
        quantity=batch_info.quantity,
        expiration_date=batch_info.expiration_date,
        batch_type='delivery' if isinstance(batch_info, BatchDeliveryInfo) else 'transfer',
        created_at=current_time
    )
    db.add(new_report_batch)

    # Then handle ProductBatch - merge quantities for same expiration date
    existing_batch = db.query(ProductBatch).filter(
        ProductBatch.branch_id == branch_id,
        ProductBatch.product_id == product_id,
        ProductBatch.expiration_date == batch_info.expiration_date,
        ProductBatch.is_active == True
    ).first()

    if existing_batch:
        existing_batch.quantity += batch_info.quantity
    else:
        new_batch = ProductBatch(
            branch_id=branch_id,
            product_id=product_id,
            quantity=batch_info.quantity,
            expiration_date=batch_info.expiration_date,
            created_at=current_time
        )
        db.add(new_batch)

def update_branch_product_quantity(db: Session, branch_id: int, product_id: int):
    """Update branch product quantity to match sum of active batches"""
    # Get branch and product first
    branch_product = db.query(BranchProduct)\
        .join(Branch)\
        .join(Product)\
        .filter(
            BranchProduct.branch_id == branch_id,
            BranchProduct.product_id == product_id
        ).first()
    
    if not branch_product:
        return
        
    # Check if product is available for this branch type
    if (branch_product.branch.branch_type == 'wholesale' and 
        not branch_product.product.is_wholesale_available):
        branch_product.is_available = False
        branch_product.low_stock_since = None
        db.commit()
        return
    elif (branch_product.branch.branch_type == 'retail' and 
          not branch_product.product.is_retail_available):
        branch_product.is_available = False
        branch_product.low_stock_since = None
        db.commit()
        return

    total_quantity = db.query(sa.func.sum(ProductBatch.quantity))\
        .filter(
            ProductBatch.branch_id == branch_id,
            ProductBatch.product_id == product_id,
            ProductBatch.is_active == True
        ).scalar() or 0
    
    old_quantity = branch_product.quantity
    branch_product.quantity = total_quantity
    
    # Check for low stock status
    threshold = (
        branch_product.product.wholesale_low_stock_threshold 
        if branch_product.branch.branch_type == 'wholesale'
        else branch_product.product.retail_low_stock_threshold
    )
    
    # Update low stock status
    if total_quantity <= threshold and branch_product.is_available:
        if not branch_product.low_stock_since:
            branch_product.low_stock_since = datetime.now()
    elif total_quantity > threshold:
        branch_product.low_stock_since = None
    
    # If quantity is changing from 0 to a positive number, make the product available
    if old_quantity == 0 and total_quantity > 0:
        branch_product.is_available = True
    
    db.commit()

@router.post('/', response_model=InvReportResponse, status_code=status.HTTP_201_CREATED)
def create_inventory_report(
    report: InvReportCreate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.PHARMACIST, UserRole.WHOLESALER]))]
):
    # Check if user is assigned to this branch
    if user['branch_id'] != report.branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only create reports for your assigned branch"
        )
    
    current_time = datetime.now()
    
    # Create report
    new_report = InvReport(
        branch_id=report.branch_id,
        created_at=current_time,
        start_date=report.start_date,
        end_date=report.end_date
    )
    db.add(new_report)
    
    # Check if product is available for this branch type
    branch = db.query(Branch).filter(Branch.id == report.branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    for item_data in report.items:
        product = db.query(Product).get(item_data.product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {item_data.product_id} not found")
            
        if branch.branch_type == 'wholesale' and not product.is_wholesale_available:
            raise HTTPException(
                status_code=400,
                detail=f"Product {product.name} is not available for wholesale branches"
            )
        elif branch.branch_type == 'retail' and not product.is_retail_available:
            raise HTTPException(
                status_code=400,
                detail=f"Product {product.name} is not available for retail branches"
            )
        
        # Create report item
        report_item = InvReportItem(
            product_id=item_data.product_id,
            beginning=item_data.beginning,
            selling_area=item_data.selling_area,
            offtake=0,  # Set to 0 initially, will update after processing batches
            current_cost=product.cost,
            current_srp=product.srp
        )
        new_report.items.append(report_item)

        # Step 1: Process all incoming stock first (deliveries and transfers)
        if item_data.delivery_batches:
            for batch in item_data.delivery_batches:
                process_batch(db, report.branch_id, item_data.product_id, batch, current_time)
                report_batch = InvReportBatch(
                    quantity=batch.quantity,
                    expiration_date=batch.expiration_date,
                    batch_type='delivery',
                    created_at=current_time
                )
                report_item.batches.append(report_batch)

        if item_data.transfer_batches:
            for batch in item_data.transfer_batches:
                process_batch(db, report.branch_id, item_data.product_id, batch, current_time)
                report_batch = InvReportBatch(
                    quantity=batch.quantity,
                    expiration_date=batch.expiration_date,
                    batch_type='transfer',
                    created_at=current_time
                )
                report_item.batches.append(report_batch)

        # Commit the incoming stock changes before processing pull-outs
        db.flush()

        # Step 2: Process pull-outs
        if item_data.pull_out_batches:
            for batch in item_data.pull_out_batches:
                existing_batch = db.query(ProductBatch).filter(
                    ProductBatch.branch_id == report.branch_id,
                    ProductBatch.product_id == item_data.product_id,
                    ProductBatch.expiration_date == batch.expiration_date,
                    ProductBatch.is_active == True
                ).first()
                
                if not existing_batch:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Batch with expiration date {batch.expiration_date} not found"
                    )
                
                if existing_batch.quantity < batch.quantity:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Insufficient quantity in batch with expiration date {batch.expiration_date}"
                    )
                
                report_batch = InvReportBatch(
                    quantity=batch.quantity,
                    expiration_date=existing_batch.expiration_date,
                    batch_type='pull_out',
                    created_at=current_time
                )
                report_item.batches.append(report_batch)
                
                existing_batch.quantity -= batch.quantity

        # Step 3: Process offtake using update_batch_quantities
        if item_data.offtake > 0:
            try:
                update_batch_quantities(db, report.branch_id, item_data.product_id, item_data.offtake)
                report_item.offtake = item_data.offtake
            except HTTPException as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Error processing offtake: {str(e)}"
                )

        # Update final quantities after all operations
        update_branch_product_quantity(db, report.branch_id, item_data.product_id)
    
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    complete_report = (
        db.query(InvReport)
        .options(joinedload(InvReport.items).joinedload(InvReportItem.batches))
        .filter(InvReport.id == new_report.id)
        .first()
    )
    
    # Record inventory metrics
    for item in complete_report.items:
        AnalyticsTimeSeries.record_metric(
            db,
            "inventory_level",
            item.selling_area,
            product_id=item.product_id,
            branch_id=complete_report.branch_id
        )
        
        if item.offtake > 0:
            AnalyticsTimeSeries.record_metric(
                db,
                "product_offtake",
                item.offtake,
                product_id=item.product_id,
                branch_id=complete_report.branch_id
            )
    
    return complete_report

@router.get('/{report_id}', response_model=InvReportResponse)
def get_inventory_report(
    report_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))]
):
    report = (
        db.query(InvReport)
        .options(
            joinedload(InvReport.items.of_type(InvReportItem))
            .joinedload(InvReportItem.product)
        )
        .filter(InvReport.id == report_id)
        .first()
    )
    
    if not report:
        raise HTTPException(status_code=404, detail="Inventory report not found")
    
    # Check if user is assigned to this branch
    if user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value] and user['branch_id'] != report.branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only view reports for your assigned branch"
        )
    
    # Automatically mark as viewed for admins
    if user['role'] == UserRole.ADMIN.value and report.viewed_by is None:
        report.viewed_by = user['id']
        db.commit()
    
    # Sort items by product name
    report.items.sort(key=lambda x: x.product.name)
    
    return report

@router.get('/', response_model=List[InvReportSummaryResponse])
def get_all_inventory_reports(
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
    skip: int = 0,
    limit: int = 100
):
    query = (
        db.query(InvReport)
        .options(
            selectinload(InvReport.branch),
            selectinload(InvReport.items).joinedload(InvReportItem.product)
        )
        .order_by(InvReport.created_at.desc())
    )
    
    # Filter by branch for non-admin users
    if user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value]:
        query = query.filter(InvReport.branch_id == user['branch_id'])
    
    reports = query.offset(skip).limit(limit).all()
    
    return reports

@router.get('/branch/{branch_id}', response_model=List[InvReportSummaryResponse])
def get_branch_inventory_reports(
    branch_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))],
    skip: int = 0,
    limit: int = 100
):
    # Check if user is assigned to this branch
    if user['role'] in [UserRole.PHARMACIST.value, UserRole.WHOLESALER.value] and user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only view reports for your assigned branch"
        )
    
    reports = (
        db.query(InvReport)
        .options(
            selectinload(InvReport.branch)
        )
        .filter(InvReport.branch_id == branch_id)
        .order_by(InvReport.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    
    return reports

class ProductBatchSummary(BaseModel):
    total_quantity: int
    expired: int
    critical: int
    warning: int

class ProductBatchesResponse(BaseModel):
    product_id: int
    name: str
    branch_id: int
    branch_name: str
    branch_type: str
    total_quantity: int
    expired: int
    critical: int
    warning: int
    batches: List[ProductBatchItem]

class BranchExpiringBatchesResponse(BaseModel):
    expired: List[ExpiringBatchItem]
    critical: List[ExpiringBatchItem]
    warning: List[ExpiringBatchItem]

@router.get('/expiring-batches/{branch_id}', response_model=BranchExpiringBatchesResponse)
def get_branch_expiring_batches(
    branch_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN, UserRole.PHARMACIST, UserRole.WHOLESALER]))]
):
    # Check if user is assigned to this branch (only for non-admin users)
    if user['role'] != UserRole.ADMIN.value and user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only view expiring batches for your assigned branch"
        )
    
    """Get all batch suggestions for a branch, grouped by expiry status"""
    batches = (db.query(ProductBatch)
        .filter(
            ProductBatch.branch_id == branch_id,
            ProductBatch.is_active == True
        )
        .order_by(ProductBatch.expiration_date)
        .all())

    # Group batches by product_id and expiration_date only
    batch_groups = {}
    for batch in batches:
        key = (batch.product_id, batch.expiration_date)
        if key not in batch_groups:
            batch_groups[key] = {
                "product_id": batch.product_id,
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
    user: Annotated[dict, Depends(role_required([UserRole.PHARMACIST, UserRole.WHOLESALER]))]
):
    # Check if user is assigned to this branch
    if user['branch_id'] != branch_id:
        raise HTTPException(
            status_code=403,
            detail="You can only view batches for your assigned branch"
        )

    # Get branch and product
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Check if product is available for this branch type
    if branch.branch_type == 'wholesale' and not product.is_wholesale_available:
        raise HTTPException(
            status_code=400,
            detail="This product is not available for wholesale branches"
        )
    elif branch.branch_type == 'retail' and not product.is_retail_available:
        raise HTTPException(
            status_code=400,
            detail="This product is not available for retail branches"
        )

    batches = (db.query(ProductBatch)
        .filter(
            ProductBatch.branch_id == branch_id,
            ProductBatch.product_id == product_id,
            ProductBatch.is_active == True
        )
        .order_by(ProductBatch.expiration_date)
        .all())

    # Group batches by expiration date only
    batch_groups = {}
    for batch in batches:
        key = batch.expiration_date
        if key not in batch_groups:
            batch_groups[key] = {
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
        "name": product.name,
        "branch_id": branch.id,
        "branch_name": branch.branch_name,
        "branch_type": branch.branch_type,
        "total_quantity": sum(b["quantity"] for b in combined_batches),
        "expired": sum(1 for b in combined_batches if b["status"] == "expired"),
        "critical": sum(1 for b in combined_batches if b["status"] == "critical"),
        "warning": sum(1 for b in combined_batches if b["status"] == "warning"),
        "batches": combined_batches
    }

@router.post('/{report_id}/mark-viewed', response_model=dict)
def mark_report_as_viewed(
    report_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.ADMIN]))],
):
    report = db.query(InvReport).filter(InvReport.id == report_id).first()
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    # Only mark as viewed if not already viewed
    if report.viewed_by is None:
        report.viewed_by = user['id']
        db.commit()
    
    return {"message": "Report marked as viewed", "viewed_by": report.viewed_by}