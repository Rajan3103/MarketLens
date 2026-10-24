"""Shared setup for all tests. pytest reads this file automatically.

1. Adds the MarketLens folder to Python's search path so tests can
   "import marketlens" no matter which folder pytest is started from.
2. Database fixtures (db_url, db_session) used by every test that needs
   PostgreSQL. They only ever use TEST_DATABASE_URL, never DATABASE_URL.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from marketlens import config  # noqa: E402
from marketlens.db import (alembic_config, connection_status, ensure_database_exists,  # noqa: E402
                           get_engine, get_session, to_psycopg_url)


# Connect to TEST_DATABASE_URL and build the tables with Alembic, or skip with a reason.
@pytest.fixture(scope="session")
def db_url():
    url = config.TEST_DATABASE_URL
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set in .env, so database tests are skipped.")
    if to_psycopg_url(url) == to_psycopg_url(config.DATABASE_URL):
        pytest.fail("TEST_DATABASE_URL must be a different database from DATABASE_URL.")
    try:
        ensure_database_exists(url)
    except Exception:
        pass  # connection_status below gives the friendly reason
    ok, message = connection_status(url)
    if not ok:
        pytest.skip(f"Test database not reachable: {message}")

    from alembic import command
    cfg = alembic_config(url)
    cfg.attributes["configure_logger"] = False  # keep our own log setup
    command.upgrade(cfg, "head")
    return url


# Empty every table before each test so tests never depend on each other.
@pytest.fixture
def db_session(db_url):
    from sqlalchemy import text

    from marketlens.models import Base
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with get_engine(db_url).begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    with get_session(db_url) as session:
        yield session
