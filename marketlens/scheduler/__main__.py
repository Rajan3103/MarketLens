"""python -m marketlens.scheduler              run every job at its time (Ctrl+C stops)
   python -m marketlens.scheduler --list       each job, its next run and its last result
   python -m marketlens.scheduler --run sync_prices [--force]   run one job now (--force: even on a holiday)
"""
import argparse
import sys

from marketlens import config
from marketlens.scheduler import jobs


# The job list as text.
def show(url=None):
    for row in jobs.job_table(url):
        nxt = f"{row['next_run']:%a %d-%b %H:%M}" if row["next_run"] else "-"
        last = f"{row['last_run']:%d-%b %H:%M} {row['status']}" if row["last_run"] else "never run"
        print(f"{row['id']:<18} {row['when']:<40} next {nxt:<18} last {last}  {row['result'] or ''}")


def main(argv=None, url=None) -> int:
    parser = argparse.ArgumentParser(description="MarketLens scheduled jobs (Asia/Kolkata).")
    parser.add_argument("--list", action="store_true", help="show each job, its next run and last result")
    parser.add_argument("--run", choices=list(jobs.BY_ID), help="run one job now and stop")
    parser.add_argument("--force", action="store_true", help="with --run: also on a holiday / outside market hours")
    args = parser.parse_args(argv)
    if args.list:
        show(url)
        return 0
    if args.run:
        status, message = jobs.run_job(args.run, url=url, force=args.force)
        print(f"{args.run}: {status} - {message}")
        return 0 if status != "failed" else 1
    scheduler = jobs.build_scheduler(url)
    print(f"MarketLens scheduler started ({config.TRADING_MODE.upper()} mode, Asia/Kolkata). "
          f"{len(jobs.JOBS)} jobs; NSE holidays from {jobs.HOLIDAYS_FILE.name}. Press Ctrl+C to stop.", flush=True)
    show(url)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
