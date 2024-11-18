from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field, validator
from datetime import datetime

from api.models import Client, Branch, BranchType, UserRole
from api.deps import db_dependency, role_required

router = APIRouter(
    prefix='/clients',
    tags=['clients']
)

class ClientBase(BaseModel):
    name: str
    tin_number: str
    markup_percentage: float = Field(ge=0, le=1)  # 0 to 1 (0% to 100%)
    payment_terms: int = Field(ge=0)  # Number of days
    credit_limit: float = Field(ge=0)
    address: str
    contact_person: str
    contact_number: str
    email: Optional[str] = None
    branch_id: int

class ClientCreate(ClientBase):
    pass

class ClientUpdate(BaseModel):
    name: Optional[str] = None
    markup_percentage: Optional[float] = Field(ge=0, le=1, default=None)
    payment_terms: Optional[int] = Field(ge=0, default=None)
    credit_limit: Optional[float] = Field(ge=0, default=None)
    address: Optional[str] = None
    contact_person: Optional[str] = None
    contact_number: Optional[str] = None
    email: Optional[str] = None
    is_active: Optional[bool] = None

class ClientResponse(ClientBase):
    id: int
    current_balance: float
    is_active: bool
    created_at: datetime
    updated_at: datetime
    available_credit: float
    is_credit_available: bool

    class Config:
        from_attributes = True 

@router.post('/', response_model=ClientResponse)
def create_client(
    client: ClientCreate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))]
):
    # Verify branch exists and is wholesale
    branch = db.query(Branch).filter(Branch.id == client.branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    if branch.branch_type != BranchType.WHOLESALE:
        raise HTTPException(
            status_code=400,
            detail="Clients can only be created for wholesale branches"
        )

    # Check if TIN number is unique
    existing_client = db.query(Client).filter(Client.tin_number == client.tin_number).first()
    if existing_client:
        raise HTTPException(
            status_code=400,
            detail="A client with this TIN number already exists"
        )

    new_client = Client(**client.model_dump())
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    return new_client

@router.get('/', response_model=List[ClientResponse])
def get_clients(
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))],
    branch_id: Optional[int] = None,
    active_only: bool = True
):
    query = db.query(Client)
    
    if branch_id:
        query = query.filter(Client.branch_id == branch_id)
    
    # Non-admin users can only see clients from their branch
    if user['role'] == UserRole.WHOLESALER.value:
        query = query.filter(Client.branch_id == user['branch_id'])
    
    if active_only:
        query = query.filter(Client.is_active == True)
    
    return query.all()

@router.get('/{client_id}', response_model=ClientResponse)
def get_client(
    client_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))]
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Check if user has access to this client's branch
    if (user['role'] == UserRole.WHOLESALER.value and 
        user['branch_id'] != client.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only view clients from your branch"
        )
    
    return client

@router.put('/{client_id}', response_model=ClientResponse)
def update_client(
    client_id: int,
    client_update: ClientUpdate,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required([UserRole.WHOLESALER, UserRole.ADMIN]))]
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Check if user has access to this client's branch
    if (user['role'] == UserRole.WHOLESALER.value and 
        user['branch_id'] != client.branch_id):
        raise HTTPException(
            status_code=403,
            detail="You can only update clients from your branch"
        )
    
    # Update fields
    for key, value in client_update.model_dump(exclude_unset=True).items():
        setattr(client, key, value)
    
    db.commit()
    db.refresh(client)
    return client

@router.delete('/{client_id}')
def delete_client(
    client_id: int,
    db: db_dependency,
    user: Annotated[dict, Depends(role_required(UserRole.ADMIN))]
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Soft delete by setting is_active to False
    client.is_active = False
    db.commit()
    return {"detail": "Client deactivated successfully"}