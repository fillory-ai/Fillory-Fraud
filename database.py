"""Database setup and session management."""
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.environ.get("DBFB9343D8_DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DBFB9343D8_DATABASE_URL environment variable not set")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)

# Overridable so tooling can point at a scratch schema (used when generating a
# baseline migration against an empty schema). Production never sets this.
SEARCH_PATH = os.environ.get("DB_SEARCH_PATH", "public, neon_auth")

@event.listens_for(engine, "connect")
def set_search_path(dbapi_connection, connection_record):
    """Ensure the public schema is in the search path."""
    cursor = dbapi_connection.cursor()
    cursor.execute(f"SET search_path TO {SEARCH_PATH}")
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
    """Bring the database up to the latest Alembic revision.

    Replaces the old `create_all()` + hand-written `ALTER TABLE ... IF NOT
    EXISTS` list. That approach could only ever add nullable columns: it could
    not express a constraint, an index, a data backfill, or a rollback — all of
    which M1's de-duplication work needs.

    A fresh database gets the baseline revision (which creates every table) and
    then every revision after it, so there is exactly one code path for new and
    existing deployments.
    """
    from alembic import command
    from alembic.config import Config

    ini_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alembic.ini")
    cfg = Config(ini_path)
    cfg.set_main_option(
        "script_location",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations"),
    )
    command.upgrade(cfg, "head")