"""Download daily prices for a whole universe into PostgreSQL.

Run it from the MarketLens folder:
    python scripts/sync_prices.py --universe nifty50 --years 10
    python scripts/sync_prices.py --universe etfs
    python scripts/sync_prices.py --universe nifty200 --years 5

The first run downloads everything (a few minutes for nifty200 x 10 years).
Later runs only download the days that are missing, so they take seconds.
Run 'python scripts/init_db.py' once before the first sync.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketlens.db import check_connection  # noqa: E402
from marketlens.ingest.prices import UNIVERSES, load_universe, sync_prices  # noqa: E402


# Read the --universe and --years options typed after the script name.
def parse_args(argv=None) -> argparse.Namespace:
    """Return the chosen options (also shows help with --help)."""
    parser = argparse.ArgumentParser(description="Sync daily prices into PostgreSQL.")
    parser.add_argument("--universe", choices=UNIVERSES, default="nifty50",
                        help="which list of symbols to sync (default: nifty50)")
    parser.add_argument("--years", type=int, default=10,
                        help="how many years of history to keep (default: 10)")
    return parser.parse_args(argv)


# Check the database, sync the chosen universe and print the summary.
def main(argv=None) -> int:
    """Returns 0 when it worked, 1 when the database is unreachable or every symbol failed."""
    args = parse_args(argv)
    if args.years < 1:
        print("PROBLEM: --years must be 1 or more")
        return 1
    if not check_connection():
        return 1

    universe = load_universe(args.universe)
    end = dt.date.today()
    try:
        start = end.replace(year=end.year - args.years)
    except ValueError:  # today is 29 Feb and that year has no 29 Feb
        start = end.replace(year=end.year - args.years, day=28)
    kind = "ETF" if args.universe == "etfs" else "EQUITY"
    names = dict(zip(universe["symbol"], universe["name"]))
    print(f"Syncing {len(universe)} symbols from '{args.universe}', {start} to {end} ...")

    summary = sync_prices(list(universe["symbol"]), start, end, instrument_type=kind, names=names)
    print(f"Done: {summary}")
    if summary.failed:
        print("Failed symbols are often renamed or newly listed; run update_universe.py "
              "or try again later.")
    return 1 if summary.failed and len(summary.failed) == summary.symbols else 0


if __name__ == "__main__":
    sys.exit(main())
