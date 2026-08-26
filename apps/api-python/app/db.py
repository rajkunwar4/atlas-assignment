import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def sqlalchemy_url(value: str) -> str:
    """Use psycopg 3 while accepting Neon's standard postgres:// URLs."""
    if value.startswith("postgres://"):
        value = "postgresql://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://atlas:atlas@localhost:55432/atlas?sslmode=disable",
)
engine = create_engine(sqlalchemy_url(DATABASE_URL), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
