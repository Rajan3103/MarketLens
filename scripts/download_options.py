"""Download option data into PostgreSQL.

Run it from the MarketLens folder. Three jobs:

  Expired options (minute bars, ATM-10 .. ATM+10, CE and PE):
    python scripts/download_options.py expired --underlying NIFTY --from 2026-09-01 --to 2026-10-23
    python scripts/download_options.py expired --underlying BANKNIFTY --flag monthly --width 5 --types CE

  Index spot minutes (NIFTY, BANKNIFTY, FINNIFTY, SENSEX):
    python scripts/download_options.py index --underlying NIFTY --from 2026-09-01 --to 2026-10-23

  Snapshot of today's option chain:
    python scripts/download_options.py snapshot --underlying NIFTY

Long downloads can be stopped with Ctrl + C and simply run again: finished days are skipped.
Without Dhan keys, DEMO data is used (and clearly reported as DEMO).
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketlens.db import check_connection  # noqa: E402
from marketlens.ingest.options import (download_expired_options, download_index_minutes,  # noqa: E402
                                       snapshot_option_chain, strike_labels)

UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]


# Read "2026-10-23" typed after --from / --to.
def a_date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date like 2026-10-23")


# All the options the script understands (shown with --help).
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download option data into PostgreSQL.")
    jobs = parser.add_subparsers(dest="job", required=True)

    expired = jobs.add_parser("expired", help="expired-option minute bars")
    index = jobs.add_parser("index", help="index spot minute bars")
    snap = jobs.add_parser("snapshot", help="save today's option chain")
    for p in (expired, index, snap):
        p.add_argument("--underlying", choices=UNDERLYINGS, default="NIFTY")
    for p in (expired, index):
        p.add_argument("--from", dest="start", type=a_date, required=True, help="first day, e.g. 2026-09-01")
        p.add_argument("--to", dest="end", type=a_date, default=dt.date.today(), help="last day (default today)")
    expired.add_argument("--flag", choices=["weekly", "monthly"], default="weekly")
    expired.add_argument("--width", type=int, default=10, help="strikes each side of ATM (default 10)")
    expired.add_argument("--types", nargs="+", choices=["CE", "PE"], default=["CE", "PE"])
    return parser.parse_args(argv)


# Print one progress line, overwriting the previous one.
def show_progress(done: int, total: int, text: str = "") -> None:
    print(f"\r  piece {done}/{total}  {text}".ljust(70), end="", flush=True)


# Run the chosen job and print a summary.
def main(argv=None) -> int:
    args = parse_args(argv)
    if not check_connection():
        return 1
    try:
        if args.job == "snapshot":
            result = snapshot_option_chain(args.underlying)
        elif args.end < args.start:
            print("PROBLEM: --to is before --from")
            return 1
        elif args.job == "index":
            result = download_index_minutes(args.underlying, args.start, args.end, progress=show_progress)
        else:
            if not 0 <= args.width <= 10:
                print("PROBLEM: --width must be 0 to 10 (Dhan allows ATM-10 .. ATM+10)")
                return 1
            result = download_expired_options(args.underlying, args.start, args.end, args.flag,
                                              strike_labels(args.width), tuple(args.types),
                                              progress=show_progress)
    except KeyboardInterrupt:
        print("\nStopped. Finished days are saved; run the same command again to continue.")
        return 1
    except ValueError as err:
        print(f"\nPROBLEM: {err}")
        return 1
    print()
    if result.get("source") == "DEMO":
        print("NOTE: Dhan keys not set, so this is DEMO data (marked is_demo in the database).")
    print(f"Done: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
