"""Alembic environment.

Deliberately ignores the sqlalchemy.url in alembic.ini and reuses the engine
from database.py, so migrations always run against exactly the same database
the app does (URL comes from the Neon connector env var) and the connection
string never has to be written to a file.
"""
import os
import sys
from logging.config import fileConfig

from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, engine  # noqa: E402
import models  # noqa: F401,E402  (import registers all tables on Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(object_, name, type_, reflected, compare_to):
    """Only ever manage tables this application declares.

    The engine's search_path includes `neon_auth`, so reflection also sees the
    Neon Auth tables (user, session, organization, ...). Without this filter
    autogenerate reads their absence from our models as "drop them" — which it
    proposed on the very first run. Anything not in Base.metadata is other
    people's data and is left strictly alone.
    """
    managed = set(target_metadata.tables)
    if type_ == "table":
        return name in managed
    parent = getattr(object_, "table", None)
    if parent is not None:
        return parent.name in managed
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
