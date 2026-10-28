"""Connection to the PostgreSQL database (PostgreSQL only, no SQLite).

How to use it:
    from marketlens.db import get_session, upsert
    from marketlens.models import DailyPrice

    with get_session() as session:
        upsert(session, DailyPrice, rows, ["instrument_id", "date"])
        session.commit()   # nothing is saved until you commit

The address and password come from DATABASE_URL in .env (see config.py).
"""
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from marketlens import config
from marketlens.logger import get_logger

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TIMEZONE = "Asia/Kolkata"
# PostgreSQL allows at most 65,535 values in one statement; stay well below it
MAX_VALUES_PER_STATEMENT = 30000
# The password written in .env.example; it must be replaced before connecting
PLACEHOLDER_PASSWORD = ":CHANGE_ME@"


# Turn any PostgreSQL address into one that uses the psycopg 3 driver.
def to_psycopg_url(url: str) -> str:
    """postgresql://... and postgres://... become postgresql+psycopg://...

    Anything that is not PostgreSQL (for example sqlite:///...) is refused.
    """
    url = (url or "").strip()
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    scheme = url.split("://")[0] if url else "(empty)"
    raise ValueError(f"MarketLens uses PostgreSQL only, but DATABASE_URL starts with '{scheme}'. "
                     "It must look like postgresql+psycopg://USER:PASSWORD@localhost:5432/marketlens")


# Build an engine: the object that opens connections to the database.
def make_engine(url: str) -> Engine:
    """pool_pre_ping checks a connection still works before using it.

    Every connection uses Indian time, so TIMESTAMPTZ values come back in IST.
    """
    return create_engine(
        to_psycopg_url(url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": f"-c timezone={TIMEZONE}"},
    )


# Return the engine for DATABASE_URL (or another address), created only once.
@lru_cache(maxsize=None)
def get_engine(url: str | None = None) -> Engine:
    """Same address -> same engine, so connections are shared and reused."""
    return make_engine(url or config.DATABASE_URL)


# Open a session: a "conversation" with the database for reading and writing.
def get_session(url: str | None = None) -> Session:
    """Use it with `with get_session() as session:` so it always closes."""
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)()


# Turn a technical database error into a simple sentence with a fix.
def explain_db_error(error_text: str, url: str) -> str:
    """Return a friendly message for the most common connection problems."""
    parts = make_url(to_psycopg_url(url))
    where = f"{parts.host or 'localhost'}:{parts.port or 5432}"
    t = error_text.lower()
    if "password authentication failed" in t:
        return (f"Wrong PostgreSQL password for user '{parts.username}'. "
                "Fix the password in DATABASE_URL in your .env file.")
    if "no password supplied" in t or "fe_sendauth" in t:
        return ("PostgreSQL asked for a password but DATABASE_URL has none. Write it as "
                "postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/marketlens in .env.")
    if 'role "' in t and "does not exist" in t:
        return f"PostgreSQL user '{parts.username}' does not exist. Check the user name in DATABASE_URL."
    if 'database "' in t and "does not exist" in t:
        return (f"Database '{parts.database}' does not exist yet. "
                "Run: python scripts/init_db.py  (it creates it for you).")
    if any(word in t for word in ("connection refused", "could not connect", "timeout expired",
                                  "connection timed out", "actively refused")):
        return (f"Cannot reach PostgreSQL at {where}. Is it running? "
                "Windows: open Services, find postgresql-x64-..., click Start. "
                "Ubuntu: sudo systemctl start postgresql")
    if any(word in t for word in ("could not translate host", "name or service not known",
                                  "nodename nor servname", "getaddrinfo")):
        return f"Unknown database host '{parts.host}'. Check the host name in DATABASE_URL."
    return f"Could not connect to PostgreSQL at {where}: {error_text.strip().splitlines()[0]}"


# Try to connect and return (worked?, message) without crashing.
def connection_status(url: str | None = None) -> tuple[bool, str]:
    """Used by the Settings page and by scripts/init_db.py."""
    url = url or config.DATABASE_URL
    if PLACEHOLDER_PASSWORD in url:
        return False, ("DATABASE_URL still has the placeholder password CHANGE_ME. "
                       "Put your real PostgreSQL password in .env and restart the app.")
    try:
        with get_engine(url).connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar()
        return True, f"Connected to PostgreSQL {version} at {config.safe_database_url(url)}"
    except ValueError as err:  # not a PostgreSQL address
        return False, str(err)
    except Exception as err:  # wrong password, server not running, ...
        message = explain_db_error(str(err), url)
        log.warning(message)
        return False, message


# Print a friendly OK / problem message and return True when connected.
def check_connection(url: str | None = None) -> bool:
    """Same as connection_status() but prints the result for scripts."""
    ok, message = connection_status(url)
    print(("OK: " if ok else "PROBLEM: ") + message)
    return ok


# Create the database named in the address if it does not exist yet.
def ensure_database_exists(url: str | None = None) -> bool:
    """Connects to the built-in 'postgres' database and runs CREATE DATABASE.

    Returns True if the database was created now, False if it already existed.
    """
    target = make_url(to_psycopg_url(url or config.DATABASE_URL))
    name = target.database
    # AUTOCOMMIT because PostgreSQL does not allow CREATE DATABASE inside a transaction
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT",
                          connect_args={"connect_timeout": 5})
    try:
        with admin.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                  {"n": name}).scalar()
            if exists:
                return False
            quoted = conn.dialect.identifier_preparer.quote(name)
            conn.execute(text(f"CREATE DATABASE {quoted}"))
            log.info("Created database %s", name)
            return True
    finally:
        admin.dispose()


# Build the Alembic settings, pointing at DATABASE_URL or another address.
def alembic_config(url: str | None = None):
    """Used by scripts/init_db.py and the tests to run migrations from Python."""
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    if url:
        # Alembic's settings file treats % as special, so %40 must be written %%40
        cfg.set_main_option("sqlalchemy.url", to_psycopg_url(url).replace("%", "%%"))
    return cfg


# Clean the rows before an upsert and work out how many fit in one statement.
def prepare_upsert_rows(rows: list[dict], conflict_cols: list[str],
                        batch_size: int = 1000) -> tuple[list[dict], int]:
    """Returns (rows without duplicates, rows per statement).

    If two rows have the same key (e.g. same instrument and date), only the
    last one is kept: PostgreSQL refuses to update the same row twice in one go.
    """
    columns = list(rows[0].keys())
    for row in rows:
        if list(row.keys()) != columns:
            raise ValueError("Every row must have the same columns in the same order.")
    missing = [c for c in conflict_cols if c not in columns]
    if missing:
        raise ValueError(f"Rows are missing the key column(s): {missing}")
    unique = {tuple(row[c] for c in conflict_cols): row for row in rows}
    per_statement = max(1, min(batch_size, MAX_VALUES_PER_STATEMENT // len(columns)))
    return list(unique.values()), per_statement


# Insert new rows and update existing ones in one go (FR-04).
def upsert(session: Session, model, rows: list[dict], conflict_cols: list[str],
           batch_size: int = 1000) -> int:
    """INSERT ... ON CONFLICT (conflict_cols) DO UPDATE, in batches.

    model:         a table class from marketlens/models.py, e.g. DailyPrice
    rows:          a list of dicts, e.g. [{"instrument_id": 1, "date": ..., "close": ...}]
    conflict_cols: the columns that make a row unique, e.g. ["instrument_id", "date"]
    Returns how many rows were sent. Call session.commit() afterwards to save them.
    """
    if not rows:
        return 0
    rows, per_statement = prepare_upsert_rows(rows, conflict_cols, batch_size)
    update_cols = [c for c in rows[0] if c not in conflict_cols]
    table = model.__table__
    for start in range(0, len(rows), per_statement):
        stmt = pg_insert(table).values(rows[start:start + per_statement])
        if update_cols:
            # "excluded" means the new values we just tried to insert
            stmt = stmt.on_conflict_do_update(index_elements=conflict_cols,
                                              set_={c: stmt.excluded[c] for c in update_cols})
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        session.execute(stmt)
    log.info("Upserted %d rows into %s", len(rows), table.name)
    return len(rows)
