from sqlalchemy import Boolean, Column, Integer, String, ForeignKey, Table, Float, Date, select, DateTime
from sqlalchemy.orm import relationship, column_property
from .database import Base, engine
from datetime import date, datetime
from enum import Enum


class UserRole(str, Enum):
    ADMIN = 'admin'
    PHARMACIST = 'pharmacist'
    WHOLESALER = 'wholesaler'

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String)
    branch_id = Column(Integer, ForeignKey('branches.id'), nullable=True)
    branch = relationship("Branch", back_populates="users")

class Branch(Base):
    __tablename__ = "branches"

    id = Column(Integer, primary_key=True, index=True)
    branch_name = Column(String)
    location = Column(String)
    is_active = Column(Boolean, default=True)
    branch_products = relationship("BranchProduct", back_populates="branch")
    users = relationship("User", back_populates="branch")

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    category = Column(String)
    cost = Column(Float)
    srp = Column(Float)
    branch_products = relationship("BranchProduct", back_populates="product")
    inv_report_items = relationship("InvReportItem", back_populates="product")

class BranchProduct(Base):
    __tablename__ = "branch_products"

    product_id = Column(Integer, ForeignKey('products.id'), primary_key=True)
    branch_id = Column(Integer, ForeignKey('branches.id'), primary_key=True)
    quantity = Column(Integer)
    
    product = relationship("Product", back_populates="branch_products")
    branch = relationship("Branch", back_populates="branch_products")
    batches = relationship(
        "ProductBatch",
        primaryjoin="and_(BranchProduct.product_id==foreign(ProductBatch.product_id), "
                   "BranchProduct.branch_id==foreign(ProductBatch.branch_id))",
        backref="branch_product"
    )

    @property
    def peso_value(self):
        return self.quantity * self.product.cost

    @property
    def current_expiration_date(self):
        if not self.batches:
            return None
        active_batches = [b for b in self.batches if b.is_active]
        if not active_batches:
            return None
        return min(b.expiration_date for b in active_batches)

class InvReport(Base):
    __tablename__ = "invreports"

    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey('branches.id'))
    created_at = Column(DateTime, default=datetime.now)
    start_date = Column(Date)
    end_date = Column(Date)
    items = relationship("InvReportItem", back_populates="invreport")

class InvReportItem(Base):
    __tablename__ = "invreport_items"

    id = Column(Integer, primary_key=True, index=True)
    invreport_id = Column(Integer, ForeignKey('invreports.id'))
    product_id = Column(Integer, ForeignKey('products.id'))
    beginning = Column(Integer)
    deliver = Column(Integer)
    transfer = Column(Integer)
    selling_area = Column(Integer)
    pull_out = Column(Integer)
    offtake = Column(Integer)
    current_cost = Column(Float)
    current_srp = Column(Float)
    invreport = relationship("InvReport", back_populates="items")
    product = relationship("Product", back_populates="inv_report_items")

    @property
    def peso_value(self):
        return self.selling_area * self.current_cost

class Expense(Base):
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    type = Column(String)
    amount = Column(Float)
    date_created = Column(Date, default=date.today)
    branch_id = Column(Integer, ForeignKey('branches.id'))
    description = Column(String)
    vendor = Column(String)

class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    phone = Column(String)
    email = Column(String)
    address = Column(String)

class ProductBatch(Base):
    __tablename__ = "product_batches"

    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey('branches.id'))
    product_id = Column(Integer, ForeignKey('products.id'))
    lot_number = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    expiration_date = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    @property
    def days_until_expiry(self):
        return (self.expiration_date - date.today()).days

    @property
    def expiry_status(self):
        days = self.days_until_expiry
        if days <= 0:
            return "expired"
        elif days <= 30:
            return "critical"
        elif days <= 90:
            return "warning"
        return "good"

# Create the tables if they don't exist
User.metadata.create_all(bind=engine)