"""Alembic settings: which database to change and which tables it should have.

The database address comes from DATABASE_URL in .env (through marketlens/config.py),
so no password is ever written in alembic.ini. Tests and scripts can point
Alembic at another database with alembic_config(url) from marketlens/db.py.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Make "import marketlens" work when alembic is run from any folder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketlens import config as ml_config  # noqa: E402
from marketlens.db import TIMEZONE, to_psycopg_url  # noqa: E402
from marketlens.models import Base  # noqa: E402

config = context.config

# Use the logging settings from alembic.ini (only when run from the command line)
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The tables Alembic compares the database with (for --autogenerate)
target_metadata = Base.metadata


# Pick the database: an address set by our own code first, otherwise DATABASE_URL.
def database_url() -> str:
    """Return the PostgreSQL address for this migration run."""
    return to_psycopg_url(config.get_main_option("sqlalchemy.url") or ml_config.DATABASE_URL)


# Write the SQL to the screen instead of running it (alembic upgrade head --sql).
def run_migrations_offline() -> None:
    """Offline mode: no database connection needed."""
    context.configure(url=database_url(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


# Connect to the database and apply the migrations.
def run_migrations_online() -> None:
    """Online mode: the normal 'alembic upgrade head'."""
    engine = create_engine(database_url(), poolclass=pool.NullPool,
                           connect_args={"connect_timeout": 5, "options": f"-c timezone={TIMEZONE}"})
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
