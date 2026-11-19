"""Shapes of the API's answers, and helpers that turn pandas tables and Plotly charts into JSON.

    df_to_records(df)   -> [{"date": "2026-11-03", "close": 2501.5, "ma50": null}, ...]
    table_of(df)        -> Table(columns=[...], rows=[...])
    fig_to_json(fig)    -> {"data": [...], "layout": {...}}   (Plotly.js draws it exactly like Python does)

JSON has no dates, no NaN and no numpy numbers, so: dates become ISO text ("2026-11-03" or
"2026-11-03T09:15:00+05:30"), NaN / NaT / infinity become null, numpy numbers become plain numbers.
"""
import base64
import datetime as dt
import json
import math
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel


class MetricCard(BaseModel):
    """One number on a card, like Streamlit's st.metric: 'Nifty 50 · 25,120 · +0.4%'."""
    label: str
    value: str | float | int | None
    delta: str | float | None = None
    help: str | None = None


class Table(BaseModel):
    """A table: the column names in order, then one dict per row."""
    columns: list[str]
    rows: list[dict[str, Any]]


class Chart(BaseModel):
    """A Plotly figure as JSON ({"data": [...], "layout": {...}}) for Plotly.js in the browser."""
    title: str | None = None
    figure: dict[str, Any]


class ErrorAnswer(BaseModel):
    """Every error the API sends: one plain-English sentence, never a Python traceback."""
    error: str


class Health(BaseModel):
    """GET /api/health."""
    status: str
    version: str
    database: str          # ok or fail
    mode: str              # PAPER or LIVE
    demo: bool             # True = DEMO data is shown (no Dhan keys)


# ---------------------------------------------------------------- market clock and symbol search

class MarketStatus(BaseModel):
    """GET /api/market/status: open, pre-open, closed or holiday, in India time."""
    state: str                     # open | pre_open | closed | holiday
    label: str                     # Open | Pre-open | Closed | Holiday
    reason: str                    # e.g. "NSE holiday: Diwali" or "After the close (15:30)"
    now: str                       # current IST time (ISO)
    next_open: str | None          # next 09:15 opening (ISO, IST)
    closes_at: str | None = None   # 15:30 today, only while open
    list_ends: str | None          # last date in data/nse_holidays.csv
    warning: str | None = None     # set when the holiday list has run out


class SymbolMatch(BaseModel):
    """One type-ahead answer."""
    symbol: str                    # RELIANCE, NIFTYBEES or ^NSEI
    yahoo: str                     # the Yahoo Finance ticker for /api/market/history (RELIANCE.NS)
    name: str
    kind: str                      # stock | etf | index
    lists: list[str]               # nifty50, nifty200, etfs (empty for an index)


class SymbolSearch(BaseModel):
    """GET /api/market/symbols?q=..."""
    query: str
    matches: list[SymbolMatch]


# ---------------------------------------------------------------- settings and scheduler

class Message(BaseModel):
    """A warning or note to show on the page."""
    level: str                     # success | info | warning | error
    text: str


class KeyRow(BaseModel):
    """One key: set or not, and what it unlocks (never the value)."""
    name: str
    set: bool
    unlocks: str


class DatabaseCheck(BaseModel):
    """The database connection (the address never shows its password)."""
    status: str                    # ok | fail
    message: str
    hint: str | None = None        # what to do when it failed


class SettingsStatus(BaseModel):
    """GET /api/settings/status: keys, trading mode, database, and the page's warnings."""
    intro: str
    keys: list[KeyRow]
    missing_keys: int
    trading_mode: str              # PAPER | LIVE
    trading_mode_message: Message
    auto_trade: bool
    demo: bool
    database: DatabaseCheck
    messages: list[Message]


class SettingRow(BaseModel):
    """One non-secret setting."""
    name: str
    value: str


class OtherSettings(BaseModel):
    """GET /api/settings/other."""
    settings: list[SettingRow]


class BackupInfo(BaseModel):
    """GET /api/settings/backups."""
    folder: str
    count: int                     # backups kept now
    keep: int                      # the newest this many are kept
    last_file: str | None
    last_time: str | None          # IST (ISO)
    size_mb: float | None
    pg_dump_found: bool
    schedule: str                  # e.g. "Sunday 10:00"


class SchedulerState(BaseModel):
    """Is the scheduler process running?"""
    state: str                     # alive | never | stale | unknown
    text: str
    start_command: str


class SchedulerJob(BaseModel):
    """One scheduled job and its last run (from the scheduler_runs table, migration 0016)."""
    id: str
    job: str                       # the job's name in words
    when: str                      # e.g. "08:30 Mon-Fri"
    next_run: str | None           # IST (ISO)
    last_run: str | None
    status: str                    # ok | failed | skipped | running | never run | unknown
    result: str


class Holiday(BaseModel):
    """One NSE trading holiday."""
    date: str
    name: str


class HolidayInfo(BaseModel):
    """The holiday list the market jobs follow."""
    count: int
    upcoming: list[Holiday]
    list_ends: str | None
    warning: str | None = None
    note: str


class SchedulerJobs(BaseModel):
    """GET /api/scheduler/jobs."""
    scheduler: SchedulerState
    database: bool                 # False = last runs unknown (no database)
    jobs: list[SchedulerJob]
    holidays: HolidayInfo


class JobStarted(BaseModel):
    """A background job was started: follow it at GET /api/jobs/{job_id}."""
    job_id: str
    job: str
    name: str


class ModuleRow(BaseModel):
    """One module on the Home page."""
    group: str
    number: int
    name: str
    what: str
    status: str


class HomeContent(BaseModel):
    """GET /api/home: the Home page's text and module table."""
    title: str
    subtitle: str
    help: str
    modules: list[ModuleRow]
    tip: str
    note: str
    historical: bool


# One cell as a JSON-friendly value.
def to_json_value(value):
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, dt.datetime)):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float, Decimal)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if value is pd.NaT or (not isinstance(value, (str, list, dict, tuple)) and pd.isna(value)):
        return None
    return value


# A pandas table as a list of JSON rows; a date index (like yfinance's) becomes a "date" column.
def df_to_records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    if not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
        if "index" in df.columns:
            df = df.rename(columns={"index": "date"})
    return [{str(k): to_json_value(v) for k, v in row.items()} for row in df.to_dict("records")]


# A pandas table as a Table answer (columns in order + rows).
def table_of(df: pd.DataFrame) -> Table:
    rows = df_to_records(df)
    columns = list(rows[0]) if rows else [str(c) for c in df.columns]
    return Table(columns=columns, rows=rows)


# A Plotly figure as JSON the browser can draw with Plotly.js (same data, same layout).
# Numbers become plain lists (Plotly 6+ would otherwise pack them as base64 "typed arrays").
def fig_to_json(fig) -> dict:
    from plotly.utils import PlotlyJSONEncoder
    return json.loads(json.dumps(_plain(fig.to_plotly_json()), cls=PlotlyJSONEncoder))


# Numpy arrays inside a figure as plain lists (dates as ISO text, NaN as null).
def _plain(obj):
    if isinstance(obj, dict) and "bdata" in obj and "dtype" in obj:      # a packed array: unpack it
        values = np.frombuffer(base64.b64decode(obj["bdata"]), dtype=np.dtype(obj["dtype"]))
        if obj.get("shape"):
            values = values.reshape([int(n) for n in str(obj["shape"]).split(",")])
        return _plain(values)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (np.ndarray, pd.Series, pd.Index)):
        values = np.asarray(obj)
        if values.dtype.kind == "M":                                  # datetime64
            return [to_json_value(v) for v in pd.to_datetime(values)]
        return [_plain(v) for v in values.tolist()]
    return to_json_value(obj)
