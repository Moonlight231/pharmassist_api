from sqlalchemy import Boolean, Column, Integer, String, ForeignKey, Table, Float, Date, select
from sqlalchemy.orm import relationship, column_property
from .database import Base, engine
from datetime import date
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
    expiration_date = Column(Date)

    product = relationship("Product", back_populates="branch_products")
    branch = relationship("Branch", back_populates="branch_products")

class InvReport(Base):
    __tablename__ = "invreports"

    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey('branches.id'))
    date_created = Column(Date, default=date.today)
    last_edit = Column(Date)
    status = Column(String, default='pending') 
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


# Create the tables if they don't exist
User.metadata.create_all(bind=engine)