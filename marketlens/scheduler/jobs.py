"""Every scheduled job, its time (Asia/Kolkata) and a record of each run. Started by python -m marketlens.scheduler.

    08:30 Mon-Fri     refresh the Nifty 50 / 200 lists and the Dhan instrument master
    08:45 Mon-Fri     check today's Dhan login; Telegram reminder if it is not valid
    09:00 Mon-Fri     sync daily prices: Nifty 200 stocks, ETFs, Nifty 50 and the Momentum 30 ETFs
    09:20 first trading day of the month   momentum and ETF rebalance signals (Telegram)
    every 5 min       NIFTY and BANKNIFTY option chain snapshots (market hours)
    every 1 min       price alerts (market hours)
    15:45 Mon-Fri     today's minute data for the options backtester (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, ATM +-10)
    15:50 Mon-Fri     end-of-day Telegram report (P&L by strategy, forward test vs backtest, trades, top movers)
    16:00 Mon-Fri     holdings snapshot (paper, and your Dhan account when the keys are set)
    Sunday 10:00      PostgreSQL backup with pg_dump into data/backups (the newest 8 are kept)

Market jobs are skipped on NSE holidays (data/nse_holidays.csv) and weekends. Every run is saved in the
scheduler_runs table (ok / failed / skipped and a short result) and shown on the Settings page; a failure
is also written to error_log (and told on Telegram, see alerts/telegram.py).
"""
import csv
import datetime as dt
import glob
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from marketlens import config
from marketlens.broker.candles import IST
from marketlens.logger import get_logger, record_error

log = get_logger(__name__)
ROOT = Path(__file__).resolve().parent.parent.parent
HOLIDAYS_FILE = ROOT / "data" / "nse_holidays.csv"
BACKUP_DIR = ROOT / "data" / "backups"
KEEP_BACKUPS = 8
TIMEZONE = "Asia/Kolkata"
HEARTBEAT = "scheduler"
CHAIN_INDICES = ("NIFTY", "BANKNIFTY")
MINUTE_INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX")
PRICE_YEARS = 10                       # a symbol seen for the first time gets this much history


class Skip(Exception):
    """Raised by a job that has nothing to do (e.g. Dhan keys not set): saved as "skipped", not "failed"."""


@dataclass
class Job:
    """One scheduled job: id, name, when (text for the page), the cron fields for APScheduler and the work."""
    id: str
    name: str
    when: str
    cron: dict                         # CronTrigger fields, e.g. {"day_of_week": "mon-fri", "hour": 8, "minute": 30}
    func: object                       # func(now, url) -> short result text
    market_days: bool = True           # skipped on weekends and NSE holidays
    market_hours: bool = False         # also skipped outside 09:15-15:30


# ---------------------------------------------------------------- NSE holidays

# Trading holidays from data/nse_holidays.csv: {date: name}. A missing file means "no holidays known".
def load_holidays(path: Path = HOLIDAYS_FILE) -> dict:
    if not Path(path).exists():
        log.warning("No NSE holiday list at %s: only weekends are skipped", path)
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {dt.date.fromisoformat(row["date"].strip()): row["holiday"].strip()
                for row in csv.DictReader(f) if row.get("date", "").strip()}


# Why `day` is not a trading day ("Saturday", "NSE holiday: Diwali-Balipratipada"), or None when it is one.
def closed_reason(day: dt.date, holidays: dict | None = None) -> str | None:
    holidays = load_holidays() if holidays is None else holidays
    if day.weekday() >= 5:
        return day.strftime("%A")
    if day in holidays:
        return f"NSE holiday: {holidays[day]}"
    return None


# True on a weekday that is not an NSE holiday.
def is_trading_day(day: dt.date, holidays: dict | None = None) -> bool:
    return closed_reason(day, holidays) is None


# True when `day` is the first trading day of its month.
def is_first_trading_day(day: dt.date, holidays: dict | None = None) -> bool:
    holidays = load_holidays() if holidays is None else holidays
    if not is_trading_day(day, holidays):
        return False
    return all(not is_trading_day(day.replace(day=d), holidays) for d in range(1, day.day))


# ---------------------------------------------------------------- the jobs (each returns a short result)

# 08:30: Nifty 50 / 200 lists from NSE, and the Dhan instrument master (Security IDs, lot sizes).
def refresh_reference_data(now: dt.datetime, url: str | None = None) -> str:
    from marketlens.broker.dhan_instruments import download_master
    from scripts.update_universe import SOURCES, refresh
    lists = {name: refresh(name) for name in SOURCES}
    parts = [f"{name} {'refreshed' if ok else 'kept (download failed)'}" for name, ok in lists.items()]
    try:
        download_master()
        parts.append("Dhan master downloaded")
    except Exception as err:
        parts.append(f"Dhan master NOT downloaded ({err}); the saved copy is used")
    return "; ".join(parts)


# 08:45: check today's Dhan login; send the Telegram reminder when it will not last the day.
def check_login(now: dt.datetime, url: str | None = None) -> str:
    from marketlens.broker.session import remind_if_needed
    text = remind_if_needed(now=now, url=url)
    return "login valid for today" if text is None else "login NOT valid: Telegram reminder sent"


# 09:00: daily prices of the stocks, ETFs, Nifty 50 and the Momentum 30 ETFs (only missing days are fetched).
def sync_daily_prices(now: dt.datetime, url: str | None = None) -> str:
    from marketlens.ingest.prices import load_universe, sync_prices
    start = now.date() - dt.timedelta(days=365 * PRICE_YEARS)
    stocks, etfs = load_universe("nifty200"), load_universe("etfs")
    steps = [("stocks", list(stocks["symbol"]), "EQUITY", dict(zip(stocks["symbol"], stocks["name"]))),
             ("ETFs", list(etfs["symbol"]), "ETF", dict(zip(etfs["symbol"], etfs["name"]))),
             ("indices", ["^NSEI"], "INDEX", {"^NSEI": "Nifty 50"}),
             ("Momentum 30 ETFs", ["MOMENTUM", "MOM30IETF"], "ETF", None)]
    parts = []
    for label, symbols, kind, names in steps:
        s = sync_prices(symbols, start, now.date(), instrument_type=kind, names=names, url=url)
        parts.append(f"{label}: {s.rows_upserted:,} rows" + (f", {len(s.failed)} failed" if s.failed else ""))
    return "; ".join(parts)


# Every 5 minutes in market hours: NIFTY and BANKNIFTY option chain snapshots (Dhan only).
def snapshot_chains(now: dt.datetime, url: str | None = None) -> str:
    from marketlens.broker.dhan_client import has_keys
    from marketlens.ingest.options import snapshot_option_chain
    if not has_keys():
        raise Skip("Dhan keys not set: no live option chain (DEMO chains are not stored)")
    parts = []
    for index in CHAIN_INDICES:
        result = snapshot_option_chain(index, url=url)
        parts.append(f"{index} {result['rows']} rows")
    return ", ".join(parts)


# Every minute in market hours: price alerts.
def check_price_alerts(now: dt.datetime, url: str | None = None) -> str:
    from marketlens.alerts.price_alerts import check_alerts
    results = check_alerts(url, now)
    hit = sum(r["triggered"] for r in results)
    return f"{len(results)} active, {hit} triggered" if results else "no active alerts"


# 15:45: today's 1-minute option data (ATM-10 .. ATM+10, calls and puts) for the options backtester.
def download_minute_data(now: dt.datetime, url: str | None = None) -> str:
    from marketlens.broker.dhan_client import has_keys
    from marketlens.ingest.options import download_expired_options, strike_labels
    from marketlens.options import expiries
    if not has_keys():
        raise Skip("Dhan keys not set: minute option data comes from Dhan only")
    day, parts, problems = now.date(), [], []
    for index in MINUTE_INDICES:
        flag = "weekly" if expiries.has_weekly(index, day) else "monthly"
        try:
            result = download_expired_options(index, day, day, flag, strike_labels(10), url=url)
            parts.append(f"{index} ({flag}): {result['rows']:,} rows")
        except Exception as err:                      # one index failing should not stop the others
            problems.append(f"{index}: {err}")
    if problems and not parts:
        raise RuntimeError("; ".join(problems))
    return "; ".join(parts + [f"FAILED {p}" for p in problems])


# 09:20 on the first trading day of the month: what the momentum and ETF strategies say to hold now.
def rebalance_signals(now: dt.datetime, url: str | None = None) -> str:
    from marketlens.alerts.telegram import notify
    lines = [f"📅 Monthly rebalance signals {now:%d-%b-%Y} (historical model output for learning, not advice)"]
    lines += _momentum_lines(url, now) + _etf_lines(url)
    notify("\n".join(lines), source="scheduler", event="signal", url=url)
    return f"{len(lines) - 1} line(s) sent to Telegram"


# The saved momentum strategies (or the default settings): today's top N.
def _momentum_lines(url, now) -> list[str]:
    from marketlens.momentum import portfolio_bt as pb
    strategies = _saved("momentum", url) or [("Default momentum (Nifty 200, top 20)", pb.StockMomentumSettings())]
    lines = []
    for name, settings in strategies:
        if isinstance(settings, dict):
            settings = pb.StockMomentumSettings.from_params(settings)
        try:
            table = pb.current_ranks(settings, now.date(), url)
            if not table.attrs.get("market_ok", True):
                lines.append(f"🚀 {name}: Nifty 50 below its average, hold {table.attrs.get('hold') or 'cash'}")
            else:
                top = table[table["in_top_n"]]["symbol"].tolist()
                lines.append(f"🚀 {name}: hold {', '.join(top) or 'nothing passed the filters'}")
        except Exception as err:
            lines.append(f"🚀 {name}: could not rank ({err})")
    return lines


# The saved ETF strategies (or the default settings): today's target weights.
def _etf_lines(url) -> list[str]:
    from marketlens.momentum import etf_bt as eb
    try:
        data = eb.load_etf_data(url=url)
    except Exception as err:
        return [f"🧺 ETF momentum: no stored ETF prices ({err})"]
    strategies = _saved("etf", url) or [("Default ETF momentum (top 2)",
                                         eb.EtfBacktestSettings(symbols=list(data.closes.columns)))]
    lines = []
    for name, settings in strategies:
        if isinstance(settings, dict):
            settings = eb.EtfBacktestSettings.from_params(settings)
        try:
            _, weights = eb.current_signal(data, settings)
            lines.append(f"🧺 {name}: " + (", ".join(f"{s} {w:.0%}" for s, w in weights.items()) or "cash"))
        except Exception as err:
            lines.append(f"🧺 {name}: could not rank ({err})")
    return lines


# Saved strategies of one kind from the Strategy Library: [(name, params)].
def _saved(kind: str, url) -> list[tuple[str, dict]]:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Strategy
    with get_session(url) as session:
        return [(s.name, {k: v for k, v in (s.params or {}).items() if k != "source"})
                for s in session.scalars(select(Strategy).where(Strategy.kind == kind, Strategy.is_active)
                                         .order_by(Strategy.name))]


# 15:50: the end-of-day report on Telegram.
def eod_report(now: dt.datetime, url: str | None = None) -> str:
    from marketlens.alerts.telegram import notify
    text = eod_report_text(now, url)
    sent = notify(text, source="scheduler", event="daily_report", url=url)
    return f"report {'sent' if sent else 'saved, not sent (see the message history)'} ({len(text.splitlines())} lines)"


# The report: P&L by strategy, orders, kill switch, alerts (bot.daily_report), forward test vs backtest, top movers.
def eod_report_text(now: dt.datetime, url: str | None = None, movers=None) -> str:
    from marketlens.alerts.bot import daily_report
    lines = [daily_report(url, now)]
    compare = _forward_vs_backtest(now.date(), url)
    lines.append("Forward test vs backtest today:" + ("".join(compare) if compare else " no forward tests today"))
    try:
        gainers, losers = (movers or _top_movers)()
        lines.append("Top movers (Nifty 50): ▲ " + ", ".join(f"{g.symbol} {g.pct_change:+.1f}%" for g in gainers.itertuples())
                     + " · ▼ " + ", ".join(f"{g.symbol} {g.pct_change:+.1f}%" for g in losers.itertuples()))
    except Exception as err:
        lines.append(f"Top movers: not available ({err})")
    return "\n".join(lines)


# Today's forward-test P&L of each strategy next to its backtest of the same day.
def _forward_vs_backtest(day: dt.date, url) -> list[str]:
    from sqlalchemy import select

    from marketlens.alerts.bot import rs
    from marketlens.db import get_session
    from marketlens.models import ForwardTestRun, Strategy
    from marketlens.trading import forward as fw
    with get_session(url) as session:
        rows = session.execute(select(ForwardTestRun.strategy_id, Strategy.name)
                               .join(Strategy, Strategy.id == ForwardTestRun.strategy_id)
                               .where(ForwardTestRun.trade_date == day)).all()
    out = []
    for sid, name in rows:
        try:
            table = fw.history(sid, url)
            today = table[table["date"] == day].iloc[-1]
            bt = today["backtest_pnl"]
            bt_text = "no backtest data" if bt is None or bt != bt else rs(float(bt))
            out.append(f"\n  {name}: forward {rs(today['forward_pnl'])} · backtest {bt_text}")
        except Exception as err:
            out.append(f"\n  {name}: could not compare ({err})")
    return out


# The 3 biggest Nifty 50 gainers and losers today.
def _top_movers():
    from marketlens.overview import get_snapshot, top_movers
    return top_movers(get_snapshot().stocks, 3)


# 16:00: today's holdings snapshot (paper, and the Dhan account when the keys are set).
def holdings_snapshot(now: dt.datetime, url: str | None = None) -> str:
    from marketlens import library as lib
    from marketlens.broker.dhan_client import has_keys
    parts = []
    accounts = [("paper", lambda: lib.paper_rows(url))]
    if has_keys():
        def live_rows():
            from marketlens.broker.dhan_data import DhanDataClient
            client = DhanDataClient()
            return lib.add_quotes(lib.dhan_rows(client.get_holdings(), client.get_positions()), client.get_quote)
        accounts.append(("live", live_rows))
    for mode, rows in accounts:
        view = lib.holdings_view(lib.stored_prev_close(rows(), now.date(), url))
        count = lib.save_snapshot(view, mode, now.date(), url)
        parts.append(f"{mode}: {count} row(s)")
    return "; ".join(parts)


# Sunday 10:00: pg_dump of the database into data/backups; only the newest KEEP_BACKUPS are kept.
def backup_database(now: dt.datetime, url: str | None = None, folder: Path = BACKUP_DIR, run=subprocess.run) -> str:
    from sqlalchemy.engine import make_url
    exe = find_pg_dump()
    if exe is None:
        raise RuntimeError("pg_dump not found. Install the PostgreSQL command line tools, add their bin folder to PATH, "
                           "or set PG_DUMP_PATH in .env")
    db = make_url(url or config.DATABASE_URL)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{db.database}_{now:%Y-%m-%d_%H%M}.dump"
    env = {**os.environ, "PGPASSWORD": db.password or ""}          # never on the command line
    command = [exe, "--format=custom", f"--file={target}", f"--host={db.host or 'localhost'}",
               f"--port={db.port or 5432}", f"--username={db.username}", db.database]
    done = run(command, env=env, capture_output=True, text=True, timeout=3600)
    if done.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed: {(done.stderr or '').strip()[:300]}")
    removed = prune_backups(folder, db.database)
    size = target.stat().st_size / 1e6 if target.exists() else 0
    return f"{target.name} ({size:,.1f} MB)" + (f"; removed {removed} old backup(s)" if removed else "")


# Keep only the newest KEEP_BACKUPS dumps of this database; returns how many were removed.
def prune_backups(folder: Path, database: str, keep: int = KEEP_BACKUPS) -> int:
    dumps = sorted(Path(folder).glob(f"{database}_*.dump"), key=lambda p: p.name, reverse=True)
    for old in dumps[keep:]:
        old.unlink()
    return max(len(dumps) - keep, 0)


# pg_dump: PG_DUMP_PATH in .env, PATH, or the usual install folders (Windows and Ubuntu).
def find_pg_dump() -> str | None:
    wanted = os.getenv("PG_DUMP_PATH", "").strip()
    if wanted:
        return wanted if Path(wanted).exists() else None
    found = shutil.which("pg_dump")
    if found:
        return found
    candidates = sorted(glob.glob(r"C:\Program Files\PostgreSQL\*\bin\pg_dump.exe")
                        + glob.glob("/usr/lib/postgresql/*/bin/pg_dump"), reverse=True)
    return candidates[0] if candidates else None


# ---------------------------------------------------------------- the list of jobs

JOBS = [
    Job("reference_data", "Refresh stock lists and the Dhan instrument master", "08:30 Mon-Fri",
        {"day_of_week": "mon-fri", "hour": 8, "minute": 30}, refresh_reference_data),
    Job("login_check", "Check the Dhan login (Telegram reminder if not valid)", "08:45 Mon-Fri",
        {"day_of_week": "mon-fri", "hour": 8, "minute": 45}, check_login),
    Job("sync_prices", "Sync daily prices (stocks, ETFs, Nifty 50)", "09:00 Mon-Fri",
        {"day_of_week": "mon-fri", "hour": 9, "minute": 0}, sync_daily_prices),
    Job("rebalance_signals", "Momentum and ETF rebalance signals", "09:20, first trading day of the month",
        {"day": "1-7", "day_of_week": "mon-fri", "hour": 9, "minute": 20}, rebalance_signals),
    Job("chain_snapshots", "NIFTY / BANKNIFTY option chain snapshots", "every 5 min, 09:15-15:30",
        {"day_of_week": "mon-fri", "hour": "9-15", "minute": "*/5"}, snapshot_chains, market_hours=True),
    Job("price_alerts", "Check price alerts", "every 1 min, 09:15-15:30",
        {"day_of_week": "mon-fri", "hour": "9-15", "minute": "*"}, check_price_alerts, market_hours=True),
    Job("minute_data", "Download today's minute option data (ATM +-10)", "15:45 Mon-Fri",
        {"day_of_week": "mon-fri", "hour": 15, "minute": 45}, download_minute_data),
    Job("eod_report", "End-of-day Telegram report", "15:50 Mon-Fri",
        {"day_of_week": "mon-fri", "hour": 15, "minute": 50}, eod_report),
    Job("holdings_snapshot", "Holdings snapshot", "16:00 Mon-Fri",
        {"day_of_week": "mon-fri", "hour": 16, "minute": 0}, holdings_snapshot),
    Job("backup", "PostgreSQL backup (pg_dump, keep 8)", "Sunday 10:00",
        {"day_of_week": "sun", "hour": 10, "minute": 0}, backup_database, market_days=False),
]
BY_ID = {job.id: job for job in JOBS}


# ---------------------------------------------------------------- running a job and keeping its record

# Run one job now (the scheduler calls this); returns (status, message). Never raises.
def run_job(job_id: str, now: dt.datetime | None = None, url: str | None = None, force: bool = False,
            holidays: dict | None = None) -> tuple[str, str]:
    """force=True runs it even on a holiday / outside market hours (the "Run now" button)."""
    from marketlens.trading.order_service import market_is_open
    job = BY_ID[job_id]
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    reason = None
    if not force and job.market_days:
        reason = closed_reason(now.date(), holidays)
        if reason is None and job.id == "rebalance_signals" and not is_first_trading_day(now.date(), holidays):
            reason = "not the first trading day of the month"
        if reason is None and job.market_hours and not market_is_open(now):
            reason = "market closed"
    if reason and job.market_hours:
        return "skipped", reason                      # every-minute jobs: no row for each quiet minute
    run_id = _start(job_id, now, url)
    if reason:
        status, message = "skipped", reason
    else:
        began = time.monotonic()
        try:
            message = str(job.func(now, url))
            status = "ok"
        except Skip as why:
            status, message = "skipped", str(why)
        except Exception as err:
            status, message = "failed", f"{type(err).__name__}: {err}"
            record_error(__name__, f"Scheduled job '{job.name}' failed: {message}", {"job": job_id}, url=url)
        message = f"{message} ({time.monotonic() - began:.1f} s)"
    _finish(run_id, status, message, url)
    log.info("Job %s: %s - %s", job_id, status, message)
    return status, message


# Save the start of a run; returns its id (None without a database).
def _start(job_id: str, now: dt.datetime, url: str | None) -> int | None:
    try:
        from marketlens.db import get_session
        from marketlens.models import SchedulerRun
        with get_session(url) as session:
            row = SchedulerRun(job_id=job_id, started_at=now, status="running")
            session.add(row)
            session.commit()
            return row.id
    except Exception as err:
        log.warning("Could not save the start of job %s: %s", job_id, err)
        return None


# Save how a run ended.
def _finish(run_id: int | None, status: str, message: str, url: str | None):
    if run_id is None:
        return
    try:
        from marketlens.db import get_session
        from marketlens.models import SchedulerRun
        with get_session(url) as session:
            row = session.get(SchedulerRun, run_id)
            row.status, row.message, row.finished_at = status, message[:2000], dt.datetime.now(IST)
            session.commit()
    except Exception as err:
        log.warning("Could not save the end of run %s: %s", run_id, err)


# Each job with its schedule, next run and last run (for the Settings page).
def job_table(url: str | None = None, now: dt.datetime | None = None, holidays: dict | None = None) -> list[dict]:
    from sqlalchemy import func, select

    from marketlens.db import get_session
    from marketlens.models import SchedulerRun
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    with get_session(url) as session:
        newest = select(SchedulerRun.job_id, func.max(SchedulerRun.id).label("id")).group_by(SchedulerRun.job_id).subquery()
        last = {r.job_id: r for r in session.scalars(select(SchedulerRun).join(newest, SchedulerRun.id == newest.c.id))}
    rows = []
    for job in JOBS:
        run = last.get(job.id)
        rows.append({"id": job.id, "job": job.name, "when": job.when, "next_run": next_run(job, now, holidays),
                     "last_run": run.started_at if run else None, "status": run.status if run else "never run",
                     "result": run.message if run else ""})
    return rows


# The next time a job will really run (weekends, holidays and the first-trading-day rule taken into account).
def next_run(job: Job, now: dt.datetime, holidays: dict | None = None, look_ahead: int = 2000) -> dt.datetime | None:
    from apscheduler.triggers.cron import CronTrigger
    from marketlens.trading.order_service import market_is_open
    holidays = load_holidays() if holidays is None else holidays
    trigger = CronTrigger(timezone=TIMEZONE, **job.cron)
    when = trigger.get_next_fire_time(None, now)
    for _ in range(look_ahead):
        if when is None:
            return None
        ok = not job.market_days or is_trading_day(when.date(), holidays)
        if ok and job.id == "rebalance_signals":
            ok = is_first_trading_day(when.date(), holidays)
        if ok and job.market_hours:
            ok = market_is_open(when)
        if ok:
            return when
        when = trigger.get_next_fire_time(when, when + dt.timedelta(seconds=1))
    return None


# The APScheduler with every job (and a heartbeat every minute for page 11's status card).
def build_scheduler(url: str | None = None, blocking: bool = True):
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from marketlens.trading.live_runner import beat
    scheduler = (BlockingScheduler if blocking else BackgroundScheduler)(timezone=TIMEZONE)
    for job in JOBS:
        scheduler.add_job(run_job, CronTrigger(timezone=TIMEZONE, **job.cron), args=[job.id], kwargs={"url": url},
                          id=job.id, name=job.name, max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(lambda: _beat(beat, url), CronTrigger(timezone=TIMEZONE, minute="*"), id="heartbeat",
                      name="heartbeat", max_instances=1, coalesce=True)
    return scheduler


# Write the scheduler's heartbeat (never raises).
def _beat(beat, url):
    try:
        beat(HEARTBEAT, f"{len(JOBS)} jobs scheduled", url)
    except Exception as err:
        log.warning("Could not write the scheduler heartbeat: %s", err)
