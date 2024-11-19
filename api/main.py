from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import auth, products, branches, branch_products, inventory_reports, clients, transactions

from .database import Base, engine

app = FastAPI()

Base.metadata.create_all(bind=engine)

origins = [
    "http://localhost:3000", # Adjust the port if your frontend runs on a different one
    "https://yourfrontenddomain.com"
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
