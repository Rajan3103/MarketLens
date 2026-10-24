"""Set up the MarketLens database in one step.

Run it from the MarketLens folder:
    python scripts/init_db.py

What it does:
  1. Creates the database named in DATABASE_URL if it does not exist yet
  2. Checks that it can connect (friendly message if not)
  3. Runs 'alembic upgrade head' (creates or updates all tables)
  4. Prints every table with its number of rows
It is safe to run again: it never deletes data.
"""
import sys
from pathlib import Path

# Make "import marketlens" work when this file is run from the scripts folder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alembic import command  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

from marketlens import config  # noqa: E402
from marketlens.db import (alembic_config, check_connection, connection_status,  # noqa: E402
                           ensure_database_exists, get_engine)


# Print each table and how many rows it has.
def print_table_counts(engine) -> None:
    """One line per table, e.g. 'daily_prices ........ 0 rows'."""
    names = sorted(inspect(engine).get_table_names())
    with engine.connect() as conn:
        for name in names:
            count = conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar()
            print(f"  {name:.<32} {count:>8,} rows")
    print(f"{len(names)} tables (alembic_version is Alembic's own bookkeeping table).")


# Run all the steps and return 0 when everything worked, 1 when something failed.
def main() -> int:
    """The steps listed at the top of this file."""
    print(f"Database: {config.safe_database_url()}")

    # Step 1: create the database if needed (only possible when PostgreSQL is reachable)
    ok, _ = connection_status()
    if not ok:
        try:
            if ensure_database_exists():
                print("Created the database.")
        except Exception:
            pass  # check_connection below prints the friendly reason

    # Step 2: connect
    if not check_connection():
        return 1

    # Step 3: create / update the tables
    print("Running migrations (alembic upgrade head) ...")
    command.upgrade(alembic_config(config.DATABASE_URL), "head")

    # Step 4: show the result
    print("Tables:")
    print_table_counts(get_engine())
    print("Done. The database is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
