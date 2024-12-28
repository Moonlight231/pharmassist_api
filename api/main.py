from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import auth, products, branches, branch_products, inventory_reports, clients, transactions, expenses, suppliers, analytics, app_management
from fastapi.staticfiles import StaticFiles

from .database import Base, engine

app = FastAPI()

app.mount("/product_images", StaticFiles(directory="static/product_images"), name="product_images")
app.mount("/apk_files", StaticFiles(directory="static/apk_files"), name="apk_files")

Base.metadata.create_all(bind=engine)

origins = [
    "http://localhost:3000", # Adjust the port if your frontend runs on a different one
    "https://pomonabatangas.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins = origins, # Allows all origins from the list
    allow_credentials = True,
    allow_methods = ["*"], # Allows all methods
    allow_headers = ["*"], # Allows all headers
)

@app.get("/")
def health_check():
    return 'Health Check Complete'

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(branches.router)
app.include_router(branch_products.router)
app.include_router(inventory_reports.router)
app.include_router(clients.router)
app.include_router(transactions.router)
app.include_router(expenses.router)
app.include_router(suppliers.router)
app.include_router(analytics.router)
app.include_router(app_management.router)