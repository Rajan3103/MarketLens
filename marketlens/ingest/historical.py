"""Historical candles: indices, index futures and stocks (module 3 downloader).

You choose: instrument (by Security ID), from date, to date and timeframe
(1, 5, 15, 25, 60 minutes or 1 day). This file
  - checks the dates,
  - asks the data client (real Dhan, or DEMO without keys - see broker/market_data.py),
    which fetches in 90-day pieces with @retry and Dhan's speed limit,
  - saves a CSV in the folder you chose, and real candles in PostgreSQL (price_bars).
"""
import datetime as dt
import re
from pathlib import Path

import pandas as pd

from marketlens import config
from marketlens.broker.candles import COLUMNS, DAILY_MINUTES, IST, date_chunks, to_frame  # noqa: F401
from marketlens.broker.dhan_instruments import Security, remember_security
from marketlens.broker.market_data import get_data_client
from marketlens.db import get_session, upsert
from marketlens.logger import get_logger
from marketlens.models import PriceBar

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# What the page shows -> what Dhan expects (minutes, or "D" for daily candles)
TIMEFRAMES = {"1 min": 1, "5 min": 5, "15 min": 15, "25 min": 25, "60 min": 60, "1 day": "D"}
INTRADAY_YEARS = 5            # Dhan: intraday data only for the last 5 years


# Timeframe label -> minutes (1440 for daily).
def timeframe_minutes(timeframe: str) -> int:
    """"5 min" -> 5, "1 day" -> 1440. Unknown labels are refused."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Timeframe must be one of {list(TIMEFRAMES)}, not {timeframe!r}")
    value = TIMEFRAMES[timeframe]
    return DAILY_MINUTES if value == "D" else value


# Make sure the dates make sense before asking Dhan.
def check_range(start: dt.date, end: dt.date, timeframe: str, today: dt.date | None = None) -> None:
    """Raises ValueError with a friendly message when the range is not possible."""
    today = today or dt.date.today()
    if end < start:
        raise ValueError("'To date' is before 'From date'.")
    if start > today:
        raise ValueError("'From date' is in the future.")
    if timeframe_minutes(timeframe) != DAILY_MINUTES:
        oldest = today.replace(year=today.year - INTRADAY_YEARS)
        if start < oldest:
            raise ValueError(f"Dhan keeps intraday candles for the last {INTRADAY_YEARS} years only. "
                             f"Choose a 'From date' on or after {oldest:%d-%b-%Y}, or use '1 day'.")


# True when the table holds DEMO candles, not real Dhan data.
def is_demo(df: pd.DataFrame) -> bool:
    return not df.empty and (df["source"] == "DEMO").any()


# Download candles for one instrument (DEMO candles when the Dhan keys are not set).
def download_candles(sec: Security, start: dt.date, end: dt.date, timeframe: str,
                     client=None, progress=None) -> pd.DataFrame:
    """Returns one table: timestamp (IST), open, high, low, close, volume, oi, source.

    progress: optional function(done, total) - the page uses it for its progress bar.
    client:   optional data client (the tests pass one); normally get_data_client().
    """
    check_range(start, end, timeframe)
    client = client or get_data_client()
    df = client.get_candles(sec, start, end, timeframe_minutes(timeframe), progress)
    log.info("%s: %d %s candles for %s (%s to %s)", client.source, len(df), timeframe,
             sec.symbol, start, end)
    return df


# Make a clear file name, e.g. NIFTY_index_5min_2026-01-01_to_2026-03-31.csv.
def csv_name(sec: Security, timeframe: str, start: dt.date, end: dt.date, demo: bool) -> str:
    """DEMO files start with DEMO_ so they can never be mistaken for real data."""
    symbol = re.sub(r"[^A-Za-z0-9-]+", "_", sec.symbol)
    tf = timeframe.replace(" ", "")
    return f"{'DEMO_' if demo else ''}{symbol}_{sec.kind}_{tf}_{start}_to_{end}.csv"


# Turn the folder typed on the page into a full path.
def resolve_folder(folder: str | Path) -> Path:
    """Relative folders (like data/downloads) are inside the MarketLens folder."""
    path = Path(str(folder).strip() or config.DOWNLOAD_DIR).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


# Save the candles as a CSV file in the chosen folder; return the file's path.
def save_csv(df: pd.DataFrame, folder: str | Path, filename: str) -> Path:
    """Creates the folder if needed. Times are written like 2026-01-02 09:15:00+05:30."""
    path = resolve_folder(folder) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


# Store real candles in PostgreSQL (price_bars). DEMO candles are never stored.
def save_to_db(df: pd.DataFrame, sec: Security, timeframe: str, url: str | None = None) -> int:
    """Returns how many candles were saved. Downloading the same range again adds no duplicates."""
    if df.empty:
        return 0
    if is_demo(df):
        raise ValueError("DEMO candles are not saved to the database (only real Dhan data is).")
    minutes = timeframe_minutes(timeframe)
    with get_session(url) as session:
        instrument_id = remember_security(session, sec)
        rows = [{
            "instrument_id": instrument_id, "timeframe": minutes, "ts": row.timestamp.to_pydatetime(),
            "open": round(float(row.open), 4), "high": round(float(row.high), 4),
            "low": round(float(row.low), 4), "close": round(float(row.close), 4),
            "volume": None if pd.isna(row.volume) else int(row.volume),
            "oi": None if pd.isna(row.oi) else int(row.oi),
        } for row in df.itertuples()]
        count = upsert(session, PriceBar, rows, ["instrument_id", "timeframe", "ts"])
        session.commit()
    return count
