from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

URL_DATABASE = "postgresql://postgres:p0m0nABatangas@localhost:5432/pharmassist"

engine = create_engine(
    URL_DATABASE,
    pool_recycle=3600,  # Recycle connections after 1 hour
    pool_pre_ping=True,  # Validates connections before using them
    connect_args={
        "options": "-c statement_timeout=60000"  # 60-second statement timeout
    }
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()