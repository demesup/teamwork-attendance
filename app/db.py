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
            "location_latitude": "FLOAT",
            "location_longitude": "FLOAT",
            "allowed_radius_meters": "INTEGER",
            "late_after_minutes": "INTEGER",
        },
        "enrollments": {
            "enrolled_at": "TIMESTAMP WITH TIME ZONE",
            "ended_at": "TIMESTAMP WITH TIME ZONE",
        },
        "absence_requests": {
            "attachment_path": "VARCHAR(64)",
            "attachment_name": "VARCHAR(255)",
            "attachment_type": "VARCHAR(50)",
        },
        "attendance_records": {
            "latitude": "FLOAT",
            "longitude": "FLOAT",
            "qr_nonce": "VARCHAR(64)",
            "ip_address": "VARCHAR(45)",
            "device_id": "VARCHAR(128)",
        },
        "attendance_code_attempts": {
            "ip_address": "VARCHAR(45)",
            "device_id": "VARCHAR(128)",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
        # create_all only builds indexes for new tables, so add the QR-nonce uniqueness here too.
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ux_attendance_records_qr_nonce ON attendance_records (qr_nonce)")
        )
