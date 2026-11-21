"""The Settings & Status page, the scheduler and Home, for the web app (the same as the Streamlit pages).

    GET  /api/settings/status              keys set or not (never their values), PAPER / LIVE, database, warnings
    GET  /api/settings/other               the non-secret settings (database addresses with the password hidden)
    POST /api/settings/db-check            check the database again
    GET  /api/settings/backups             newest backup and how many are kept
    GET  /api/settings/cost-fields         the trading-cost boxes (COST_FIELDS) for every backtest form
    GET  /api/scheduler/jobs               each job: when, next run (IST), last run, status, result; holidays
    POST /api/scheduler/jobs/{name}/run    run one job now as a background job (follow /api/jobs/{job_id})
    GET  /api/home                         the Home page's text and module table

What is shown is worked out in marketlens/settings_info.py, market_clock.py and home.py. No route here ever
returns a key, token, password or secret (tests/test_settings_api.py checks every route for that).
"""
import datetime as dt
from functools import partial

from fastapi import APIRouter, Request

from marketlens import config, home, settings_info
from marketlens.api.errors import ApiError
from marketlens.api.jobs import start_job
from marketlens.api.schemas import (BackupInfo, DatabaseCheck, HolidayInfo, HomeContent, JobStarted, Message,
                                    OtherSettings, SchedulerJobs, SettingsStatus, to_json_value)
from marketlens.broker.candles import IST
from marketlens.market_clock import holiday_note, list_end, list_warning, upcoming_holidays

router = APIRouter()


# The database address the app was started with (tests pass the test database).
def _url(request: Request) -> str | None:
    return getattr(request.app.state, "url", None)


# The database check in the answer's shape.
def _database(url: str | None) -> DatabaseCheck:
    found = settings_info.database_status(url)
    return DatabaseCheck(status="ok" if found["ok"] else "fail", message=found["message"], hint=found["hint"])


# Keys, trading mode and database, for the frontend's Settings page.
@router.get("/api/settings/status", response_model=SettingsStatus, tags=["settings"])
def settings_status(request: Request):
    """Which keys are set (never their values), the trading mode, the database and the page's warnings."""
    from marketlens.broker.dhan_client import has_keys
    keys = settings_info.key_rows()
    level, text = settings_info.trading_mode_message()
    return SettingsStatus(
        intro=settings_info.INTRO, keys=keys, missing_keys=sum(1 for k in keys if not k["set"]),
        trading_mode=config.TRADING_MODE.upper(), trading_mode_message=Message(level=level, text=text),
        auto_trade=config.AUTO_TRADE, demo=bool(config.DEMO_MODE and not has_keys()),
        database=_database(_url(request)), messages=settings_info.page_messages())


# The "Other settings" table.
@router.get("/api/settings/other", response_model=OtherSettings, tags=["settings"])
def other_settings():
    """Only settings that are safe to show: never a key, password, token or a database password."""
    return OtherSettings(settings=settings_info.other_settings())


# The "Check again" button.
@router.post("/api/settings/db-check", response_model=DatabaseCheck, tags=["settings"])
def db_check(request: Request):
    """Connect to the database again and say how it went (the password is never shown)."""
    return _database(_url(request))


# The newest backup in data/backups.
@router.get("/api/settings/backups", response_model=BackupInfo, tags=["settings"])
def backups():
    """When the last backup was made, how big it is and how many are kept (the newest 8)."""
    return BackupInfo(**settings_info.backup_info())


# The holiday list the market jobs follow, with the next three and a warning when it has run out.
def _holidays(today: dt.date) -> HolidayInfo:
    from marketlens.scheduler.jobs import load_holidays
    holidays = load_holidays()
    end = list_end(holidays)
    return HolidayInfo(count=len(holidays),
                       upcoming=[{"date": d.isoformat(), "name": n} for d, n in upcoming_holidays(today, holidays)],
                       list_ends=end.isoformat() if end else None, warning=list_warning(today, holidays),
                       note=holiday_note(today, holidays))


# The "Scheduled jobs" section.
@router.get("/api/scheduler/jobs", response_model=SchedulerJobs, tags=["scheduler"])
def scheduler_jobs(request: Request):
    """Every job with when it runs, its next run (IST), last run, status and result. Without a database the jobs
    and next runs are still listed, with database false and status "unknown"."""
    url = _url(request)
    now = dt.datetime.now(IST)
    rows, has_db = settings_info.job_rows(url, now)
    jobs = [{**row, "next_run": to_json_value(row["next_run"]), "last_run": to_json_value(row["last_run"]),
             "result": row["result"] or ""} for row in rows]
    return SchedulerJobs(scheduler=settings_info.scheduler_status(url, now, has_db), database=has_db, jobs=jobs,
                         holidays=_holidays(now.date()))


# The background work behind "Run now".
def run_job_work(job, name: str, url: str | None) -> dict:
    from marketlens.scheduler import jobs as sched
    job.progress(5, f"Running {sched.BY_ID[name].name}")
    status, result = sched.run_job(name, url=url, force=True)
    return {"job": name, "status": status, "result": result}


# The "Run now" button: runs even on a holiday or outside market hours, like the Streamlit button.
@router.post("/api/scheduler/jobs/{name}/run", response_model=JobStarted, tags=["scheduler"])
def run_job_now(name: str, request: Request):
    """Start one job now in the background; follow it at GET /api/jobs/{job_id}."""
    from marketlens.scheduler import jobs as sched
    if name not in sched.BY_ID:
        raise ApiError(f"No job called '{name}'. Jobs: {', '.join(sched.BY_ID)}", 404)
    url = _url(request)
    try:
        job_id = start_job("scheduler_run", partial(run_job_work, url=url), {"name": name}, url=url)
    except Exception:
        raise ApiError("Running a job needs the database (every run is saved there). Start PostgreSQL and try "
                       "again.", 503) from None
    return JobStarted(job_id=job_id, job=name, name=sched.BY_ID[name].name)


# The "Trading costs" boxes every backtester, simulator and paper page shows (same as Streamlit's).
@router.get("/api/settings/cost-fields", tags=["settings"])
def cost_fields() -> dict:
    """Each box: name, label, unit, kind (number / bool / choice), default, min, step, choices and help. Send the
    values back as "costs": {...} to any backtest route; costs.cost_settings_from_dict checks them."""
    from marketlens.costs import COST_FIELDS
    return {"fields": COST_FIELDS}


# The Home page.
@router.get("/api/home", response_model=HomeContent, tags=["home"])
def home_page():
    """Title, help note and the twelve modules with what each does."""
    return HomeContent(**home.home_content())
