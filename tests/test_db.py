"""Tests for marketlens/db.py and marketlens/models.py.

The database tests use TEST_DATABASE_URL from .env, never DATABASE_URL.
They are skipped (with the reason shown) when TEST_DATABASE_URL is empty or
the test database cannot be reached. The first few tests need no database.

Run them with:  python -m pytest tests/test_db.py -v -rs
(-rs prints the reason for every skipped test)
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, inspect, select, text

from marketlens import config
from marketlens.db import (alembic_config, connection_status, ensure_database_exists,
                           explain_db_error, get_engine, get_session, prepare_upsert_rows,
                           to_psycopg_url, upsert)
from marketlens.models import Base, DailyPrice, Instrument

URL = "postgresql+psycopg://ml_user:pw@localhost:5432/marketlens"


# ---------------------------------------------------------------------
# Tests that need no database
# ---------------------------------------------------------------------

# Any PostgreSQL address is changed to use the psycopg 3 driver.
@pytest.mark.parametrize("given", [
    "postgresql://u:p@h:5432/db", "postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db",
])
def test_to_psycopg_url(given):
    assert to_psycopg_url(given) == "postgresql+psycopg://u:p@h:5432/db"


# SQLite (or anything else) is refused, because MarketLens is PostgreSQL only.
@pytest.mark.parametrize("given", ["sqlite:///data/marketlens.db", "mysql://u:p@h/db", ""])
def test_non_postgres_is_refused(given):
    with pytest.raises(ValueError, match="PostgreSQL only"):
        to_psycopg_url(given)


# Common connection errors become simple sentences with a fix.
@pytest.mark.parametrize("error, expected", [
    ('FATAL:  password authentication failed for user "ml_user"', "Wrong PostgreSQL password"),
    ("connection to server at \"localhost\", port 5432 failed: Connection refused", "Is it running?"),
    ('FATAL:  database "marketlens" does not exist', "does not exist yet"),
    ('FATAL:  role "ml_user" does not exist', "user 'ml_user' does not exist"),
    ("fe_sendauth: no password supplied", "has none"),
])
def test_explain_db_error(error, expected):
    message = explain_db_error(error, URL)
    assert expected in message
    assert ":pw@" not in message  # the password is never repeated back


# Duplicate keys inside one call are merged, keeping the last row.
def test_prepare_upsert_rows_removes_duplicates():
    rows = [{"instrument_id": 1, "date": "d1", "close": 10},
            {"instrument_id": 1, "date": "d2", "close": 11},
            {"instrument_id": 1, "date": "d1", "close": 12}]
    clean, _ = prepare_upsert_rows(rows, ["instrument_id", "date"])
    assert len(clean) == 2
    assert {r["close"] for r in clean} == {11, 12}


# Big uploads are split so one statement stays under PostgreSQL's 65,535-value limit.
def test_prepare_upsert_rows_batch_size():
    wide_row = {f"c{i}": i for i in range(100)}  # 100 columns
    _, per_statement = prepare_upsert_rows([wide_row], ["c0"], batch_size=1000)
    assert per_statement * 100 <= 65535


# Rows with different columns are refused with a clear message.
def test_prepare_upsert_rows_needs_same_columns():
    with pytest.raises(ValueError, match="same columns"):
        prepare_upsert_rows([{"a": 1, "b": 2}, {"a": 1}], ["a"])


# The models use the PostgreSQL types the design asks for.
def test_model_column_types():
    prices = Base.metadata.tables["daily_prices"]
    assert type(prices.c.close.type).__name__ == "Numeric"
    quotes = Base.metadata.tables["intraday_option_prices"]
    assert quotes.c.ts.type.timezone is True          # TIMESTAMPTZ
    assert type(Base.metadata.tables["strategies"].c.params.type).__name__ == "JSONB"
    assert len(Base.metadata.tables) == 24


# ---------------------------------------------------------------------
# Tests that use the TEST database
# ---------------------------------------------------------------------

# Connect to TEST_DATABASE_URL, build the tables with Alembic, or skip with a reason.
@pytest.fixture(scope="module")
def test_url():
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
    command.upgrade(alembic_config(url), "head")
    return url


# Empty every table before each test so tests never depend on each other.
@pytest.fixture
def session(test_url):
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with get_engine(test_url).begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    with get_session(test_url) as s:
        yield s


# Make one instrument row and return its id.
def add_instrument(session) -> int:
    reliance = Instrument(symbol="RELIANCE", instrument_type="EQUITY", yf_ticker="RELIANCE.NS")
    session.add(reliance)
    session.commit()
    return reliance.id


# Alembic created every table from models.py.
def test_migration_created_all_tables(test_url):
    names = set(inspect(get_engine(test_url)).get_table_names())
    assert set(Base.metadata.tables) <= names
    assert "alembic_version" in names


# The main test: insert two prices, re-insert one, and check there is no duplicate.
def test_upsert_does_not_duplicate(session):
    inst = add_instrument(session)
    day1, day2 = dt.date(2026, 10, 20), dt.date(2026, 10, 21)
    rows = [
        {"instrument_id": inst, "date": day1, "open": 2800, "high": 2850, "low": 2790, "close": 2840},
        {"instrument_id": inst, "date": day2, "open": 2840, "high": 2860, "low": 2820, "close": 2855.5},
    ]
    upsert(session, DailyPrice, rows, ["instrument_id", "date"])
    session.commit()

    # Same day again with a corrected close price
    fixed = [{"instrument_id": inst, "date": day2, "open": 2840, "high": 2860, "low": 2820,
              "close": 2857.25}]
    upsert(session, DailyPrice, fixed, ["instrument_id", "date"])
    session.commit()

    count = session.scalar(select(func.count()).select_from(DailyPrice))
    assert count == 2  # still two rows, not three
    close = session.scalar(select(DailyPrice.close).where(DailyPrice.date == day2))
    assert close == Decimal("2857.25")  # updated, and stored exactly (NUMERIC)


# Thousands of rows in one call work (FR-04).
def test_upsert_thousands_of_rows(session):
    inst = add_instrument(session)
    start = dt.date(2000, 1, 1)
    rows = [{"instrument_id": inst, "date": start + dt.timedelta(days=i),
             "open": 100, "high": 101, "low": 99, "close": 100 + i % 7} for i in range(5000)]
    assert upsert(session, DailyPrice, rows, ["instrument_id", "date"]) == 5000
    upsert(session, DailyPrice, rows, ["instrument_id", "date"])  # all 5000 again
    session.commit()
    assert session.scalar(select(func.count()).select_from(DailyPrice)) == 5000


# Times come back in Indian time (Asia/Kolkata).
def test_timestamps_are_ist(test_url):
    with get_engine(test_url).connect() as conn:
        assert conn.execute(text("SHOW timezone")).scalar() == "Asia/Kolkata"
        now = conn.execute(text("SELECT now()")).scalar()
    assert now.utcoffset() == dt.timedelta(hours=5, minutes=30)
