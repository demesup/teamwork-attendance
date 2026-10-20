from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


url = get_settings().database_url
connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_additive_schema() -> None:
    """Add columns introduced after the initial development database was created."""
    additions = {
        "attendance_sessions": {
            "title": "VARCHAR(200)",
            "location_latitude": "FLOAT",
            "location_longitude": "FLOAT",
            "allowed_radius_meters": "INTEGER",
        },
        "attendance_records": {
            "latitude": "FLOAT",
            "longitude": "FLOAT",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
