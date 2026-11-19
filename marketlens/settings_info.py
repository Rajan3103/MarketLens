"""What the Settings & Status page shows, worked out once for Streamlit and the web API (no Streamlit here).

    page_messages()        warnings: no password, AUTO_TRADE on, a short webhook secret, keys missing
    trading_mode_message() the PAPER / LIVE banner text
    key_rows()             each key: set or not, and what it unlocks (never the key itself)
    other_settings()       the non-secret settings (database addresses with the password hidden)
    scheduler_status()     is the scheduler process running? (from its heartbeat)
    job_rows()             each scheduled job: when, next run, last run, status, result
    backup_info()          the newest backup file and how many are kept

Nothing here ever returns a key, token, password or secret.
"""
import datetime as dt
from pathlib import Path

from marketlens import config
from marketlens.broker.candles import IST

INTRO = ("Keys are read from the .env file on this computer. .env is listed in .gitignore, so it is never uploaded "
         "to GitHub. Copy .env.example to .env to start, and restart the app after editing it.")
DB_HINT = "First time? Run `python scripts/init_db.py` in the terminal to create the database."
SCHEDULER_START = "python -m marketlens.scheduler"
NEEDS_DB = "Needs the database: the scheduler saves every run there."
NEW_SECRET = "python -c \"import secrets; print(secrets.token_urlsafe(32))\""


# The PAPER / LIVE banner: ("success" or "error", text).
def trading_mode_message() -> tuple[str, str]:
    if config.TRADING_MODE == "live":
        return "error", "Mode: LIVE. Orders use real money."
    return "success", "Mode: PAPER. Orders use virtual money only. Set TRADING_MODE=live in .env to change."


# Each key as {"name", "set", "unlocks"} (the value itself is never read out).
def key_rows() -> list[dict]:
    return [{"name": name, "set": done, "unlocks": unlocks} for name, done, unlocks in config.key_status()]


# The warnings and notes at the top of the page: [{"level": "warning" | "info", "text": ...}, ...].
def page_messages() -> list[dict]:
    out = []
    if not config.is_set(config.DASHBOARD_PASSWORD):
        out.append({"level": "warning", "text": (
            "DASHBOARD_PASSWORD is empty: anyone who can open this app can see your data and press its buttons "
            "(kill switch, deploy, square off). Set DASHBOARD_PASSWORD in .env and restart the app, especially "
            "before running it on a server.")})
    if config.AUTO_TRADE:
        out.append({"level": "warning", "text": "AUTO_TRADE is on: strategies may place orders without asking you."})
    missing = sum(1 for row in key_rows() if not row["set"])
    if missing:
        out.append({"level": "info", "text": (
            f"{missing} key(s) not set. Pages that need them show DEMO data or a 'key not set' message. "
            "DHAN_API_KEY / DHAN_API_SECRET are optional: a token pasted from web.dhan.co is enough.")})
    if config.is_set(config.WEBHOOK_SECRET) and not config.webhook_secret_strong():
        out.append({"level": "warning", "text": (
            f"WEBHOOK_SECRET is shorter than {config.WEBHOOK_SECRET_MIN_LENGTH} characters, so it is easy to "
            f"guess. Make a new one: `{NEW_SECRET}`")})
    return out


# The non-secret settings, in the page's order: [{"name", "value"}]. Database addresses hide their password.
def other_settings() -> list[dict]:
    values = {
        "TRADING_MODE": config.TRADING_MODE,
        "PAPER_CAPITAL": f"Rs {config.PAPER_CAPITAL:,.0f}",
        "AUTO_TRADE": config.AUTO_TRADE,
        "MAX_DAILY_LOSS_RS": f"Rs {config.MAX_DAILY_LOSS_RS:,.0f}",
        "MAX_ORDERS_PER_SECOND": config.MAX_ORDERS_PER_SECOND,
        "MAX_OPEN_POSITIONS": config.MAX_OPEN_POSITIONS,
        "DEMO_MODE": config.DEMO_MODE,
        "DATA_SOURCE": config.DATA_SOURCE,
        "DOWNLOAD_DIR": config.DOWNLOAD_DIR,
        "API_PORT": config.API_PORT,
        "DATABASE_URL": config.safe_database_url(),
        "TEST_DATABASE_URL": config.safe_database_url(config.TEST_DATABASE_URL) or "(not set)",
        "SERVER_STATIC_IP": config.SERVER_STATIC_IP or "(not set - Dhan needs it whitelisted for live orders)",
    }
    return [{"name": name, "value": str(value)} for name, value in values.items()]


# The database check as {"ok", "message", "hint"} (hint only when it failed).
def database_status(url: str | None = None) -> dict:
    from marketlens.db import connection_status
    ok, message = connection_status(url)
    return {"ok": ok, "message": message, "hint": None if ok else DB_HINT}


# Is the scheduler process running? {"state": "alive" | "never" | "stale" | "unknown", "text", "start_command"}.
# database_ok=False skips the database (it is already known to be down, so we don't wait for it twice).
def scheduler_status(url: str | None = None, now: dt.datetime | None = None, database_ok: bool = True) -> dict:
    from marketlens.scheduler import jobs as sched
    from marketlens.trading import live_runner as lr
    from marketlens.trading import monitor as mon
    try:
        if not database_ok:
            raise ConnectionError("no database")
        state, text = mon.heartbeat_state(lr.heartbeats(url).get(sched.HEARTBEAT), now=now)
    except Exception:
        state, text = "unknown", NEEDS_DB
    if state == "alive":
        text = f"Scheduler running: {text}"
    elif state == "never":
        text = f"Scheduler not started. Start it in its own terminal: {SCHEDULER_START}"
    elif state == "stale":
        text = f"Scheduler stopped ({text}). Start it in its own terminal: {SCHEDULER_START}"
    return {"state": state, "text": text, "start_command": SCHEDULER_START}


# Each job with when it runs, its next run and its last run. Without a database the last runs are unknown
# (database False) but the list and next runs are still there.
def job_rows(url: str | None = None, now: dt.datetime | None = None, holidays: dict | None = None) -> tuple[list, bool]:
    from marketlens.scheduler import jobs as sched
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    try:
        return sched.job_table(url, now, holidays), True
    except Exception:
        rows = [{"id": job.id, "job": job.name, "when": job.when, "next_run": sched.next_run(job, now, holidays),
                 "last_run": None, "status": "unknown", "result": NEEDS_DB} for job in sched.JOBS]
        return rows, False


# The newest backup and how many are kept: {"folder", "count", "keep", "last_file", "last_time", "size_mb", ...}.
def backup_info(folder: Path | None = None, database: str | None = None) -> dict:
    from sqlalchemy.engine import make_url

    from marketlens.scheduler import jobs as sched
    folder = Path(folder or sched.BACKUP_DIR)
    if database is None:
        try:
            database = make_url(config.DATABASE_URL).database or "marketlens"
        except Exception:
            database = "marketlens"
    dumps = sorted(folder.glob(f"{database}_*.dump"), key=lambda p: p.name, reverse=True) if folder.exists() else []
    newest = dumps[0] if dumps else None
    return {"folder": str(folder.relative_to(sched.ROOT)) if folder.is_relative_to(sched.ROOT) else folder.name,
            "count": len(dumps), "keep": sched.KEEP_BACKUPS,
            "last_file": newest.name if newest else None,
            "last_time": (dt.datetime.fromtimestamp(newest.stat().st_mtime, IST).isoformat(timespec="seconds")
                          if newest else None),
            "size_mb": round(newest.stat().st_size / 1e6, 1) if newest else None,
            "pg_dump_found": sched.find_pg_dump() is not None,
            "schedule": sched.BY_ID["backup"].when}
