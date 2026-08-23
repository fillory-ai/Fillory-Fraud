"""Database setup and session management."""
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.environ.get("DBFB9343D8_DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DBFB9343D8_DATABASE_URL environment variable not set")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)

@event.listens_for(engine, "connect")
def set_search_path(dbapi_connection, connection_record):
    """Ensure the public schema is in the search path."""
    cursor = dbapi_connection.cursor()
    cursor.execute("SET search_path TO public, neon_auth")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_session():
    """Get a new database session. Use with SessionLocal() directly for sync code."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def db_session():
    """Convenience context manager for sync endpoints."""
    session = SessionLocal()
    try:
        return session
    finally:
        session.close()


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    _run_migrations()


# Additive, idempotent column migrations. SQLAlchemy's create_all() will not
# alter existing tables, so new columns are added explicitly here.
_MIGRATIONS = (
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS geocoded_at TIMESTAMPTZ",
    "ALTER TABLE scraped_listings ADD COLUMN IF NOT EXISTS street_address VARCHAR(300)",
    "ALTER TABLE scraped_listings ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION",
    "ALTER TABLE scraped_listings ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION",
    "ALTER TABLE scraped_listings ADD COLUMN IF NOT EXISTS enriched BOOLEAN DEFAULT FALSE",
)


def _run_migrations():
    from sqlalchemy import text
    with engine.begin() as conn:
        for statement in _MIGRATIONS:
            conn.execute(text(statement))