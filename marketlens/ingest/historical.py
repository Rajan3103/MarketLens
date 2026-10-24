"""Historical candles from Dhan: indices, index futures and stocks.

You choose: instrument (by Security ID), from date, to date and timeframe
(1, 5, 15, 25, 60 minutes or 1 day). This file
  - splits long intraday ranges into 90-day pieces (Dhan's limit per request),
  - downloads each piece (with @retry and the 5-per-second speed limit),
  - joins them into one table with times in Indian time (IST),
  - saves a CSV in the folder you chose, and the candles in PostgreSQL (price_bars).
Without Dhan keys (and DEMO_MODE=true) it makes clearly labelled DEMO candles instead.
"""
import datetime as dt
import re
import zlib
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from marketlens import config
from marketlens.broker.dhan_client import DhanError, fetch_daily, fetch_intraday, get_dhan
from marketlens.db import get_session, upsert
from marketlens.ingest.dhan_master import Security, remember_security
from marketlens.logger import get_logger
from marketlens.models import PriceBar

log = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# What the page shows -> what Dhan expects (minutes, or "D" for daily candles)
TIMEFRAMES = {"1 min": 1, "5 min": 5, "15 min": 15, "25 min": 25, "60 min": 60, "1 day": "D"}
DAILY_MINUTES = 1440          # how daily candles are stored in price_bars.timeframe
MAX_DAYS_PER_REQUEST = 90     # Dhan: at most 90 days of intraday data per request
INTRADAY_YEARS = 5            # Dhan: intraday data only for the last 5 years
MARKET_OPEN, MARKET_CLOSE = dt.time(9, 15), dt.time(15, 30)
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi", "source"]
# Starting prices for DEMO data (so the charts look roughly realistic)
DEMO_START_PRICE = {"NIFTY": 25000, "BANKNIFTY": 56000, "FINNIFTY": 26000, "SENSEX": 82000}


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


# Split a date range into pieces of at most `days` days (both ends included).
def date_chunks(start: dt.date, end: dt.date, days: int = MAX_DAYS_PER_REQUEST) -> list[tuple]:
    """1-Jan to 30-Apr with 90 days -> [(1-Jan, 31-Mar), (1-Apr, 30-Apr)]: no gaps, no overlaps."""
    chunks, first = [], start
    while first <= end:
        last = min(first + dt.timedelta(days=days - 1), end)
        chunks.append((first, last))
        first = last + dt.timedelta(days=1)
    return chunks


# Turn Dhan's answer (lists of numbers) into a table with IST times.
def to_frame(data: dict) -> pd.DataFrame:
    """Dhan sends open/high/low/close/volume/timestamp (epoch seconds) and open_interest lists."""
    if not data or not data.get("timestamp"):
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(data["timestamp"], unit="s", utc=True).tz_convert(IST),
        "open": data["open"], "high": data["high"], "low": data["low"], "close": data["close"],
        "volume": data.get("volume") or [None] * len(data["timestamp"]),
        "oi": data.get("open_interest") or [None] * len(data["timestamp"]),
    })
    df["source"] = "dhan"
    return df[COLUMNS]


# Make DEMO candles (a random walk in market hours) when there are no Dhan keys.
def demo_candles(sec: Security, start: dt.date, end: dt.date, minutes: int) -> pd.DataFrame:
    """Same columns as real data; every row says source = DEMO. Same inputs -> same candles."""
    days = pd.bdate_range(start, end)
    if minutes == DAILY_MINUTES:
        stamps = [pd.Timestamp(d.date(), tz=IST) for d in days]
    else:
        session_minutes = (dt.datetime.combine(start, MARKET_CLOSE)
                           - dt.datetime.combine(start, MARKET_OPEN)).total_seconds() / 60
        per_day = int(np.ceil(session_minutes / minutes))  # 60 min -> 7 candles, last one 15:15
        stamps = [pd.Timestamp(dt.datetime.combine(d.date(), MARKET_OPEN), tz=IST)
                  + pd.Timedelta(minutes=minutes * k) for d in days for k in range(per_day)]
    if not stamps:
        return pd.DataFrame(columns=COLUMNS)
    rng = np.random.default_rng(zlib.crc32(f"{sec.security_id}{start}{minutes}".encode()))
    step = 0.01 if minutes == DAILY_MINUTES else 0.001 * np.sqrt(minutes)
    close = DEMO_START_PRICE.get(sec.symbol, 1000) * np.exp(np.cumsum(rng.normal(0, step, len(stamps))))
    open_ = np.r_[close[0], close[:-1]]
    wiggle = np.abs(rng.normal(0, step / 2, len(stamps))) * close
    df = pd.DataFrame({
        "timestamp": stamps, "open": open_.round(2), "close": close.round(2),
        "high": (np.maximum(open_, close) + wiggle).round(2),
        "low": (np.minimum(open_, close) - wiggle).round(2),
        "volume": rng.integers(1_000, 100_000, len(stamps)),
        "oi": rng.integers(100_000, 1_000_000, len(stamps)) if sec.kind == "future" else None,
    })
    df["source"] = "DEMO"
    return df[COLUMNS]


# True when the table holds DEMO candles, not real Dhan data.
def is_demo(df: pd.DataFrame) -> bool:
    return not df.empty and (df["source"] == "DEMO").any()


# Download candles for one instrument (or make DEMO candles without keys).
def download_candles(sec: Security, start: dt.date, end: dt.date, timeframe: str,
                     dhan=None, progress=None) -> pd.DataFrame:
    """Returns one table: timestamp (IST), open, high, low, close, volume, oi, source.

    progress: optional function(done, total) - the page uses it for its progress bar.
    dhan:     optional client (the tests pass a fake one).
    """
    check_range(start, end, timeframe)
    minutes = timeframe_minutes(timeframe)
    dhan = dhan or get_dhan()
    if dhan is None:
        if not config.DEMO_MODE:
            raise DhanError("Dhan key not set (DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN). "
                            "Add them to .env, or set DEMO_MODE=true to try DEMO data.")
        log.info("No Dhan keys: making DEMO candles for %s", sec.symbol)
        if progress:
            progress(1, 1)
        return demo_candles(sec, start, end, minutes)

    # Daily data has no 90-day limit, so it is one request
    chunks = [(start, end)] if minutes == DAILY_MINUTES else date_chunks(start, end)
    pieces = []
    for i, (first, last) in enumerate(chunks, start=1):
        # Dhan leaves out the to_date itself, so ask for one day more
        to_date = (last + dt.timedelta(days=1)).isoformat()
        if minutes == DAILY_MINUTES:
            data = fetch_daily(dhan, sec.security_id, sec.segment, sec.instrument, first.isoformat(), to_date)
        else:
            data = fetch_intraday(dhan, sec.security_id, sec.segment, sec.instrument,
                                  first.isoformat(), to_date, minutes)
        pieces.append(to_frame(data))
        if progress:
            progress(i, len(chunks))
    df = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=COLUMNS)
    if df.empty:
        return df
    # Keep only the chosen dates, without repeats, oldest first
    dates = df["timestamp"].dt.date
    df = df[(dates >= start) & (dates <= end)].drop_duplicates("timestamp").sort_values("timestamp")
    log.info("Downloaded %d %s candles for %s (%s to %s)", len(df), timeframe, sec.symbol, start, end)
    return df.reset_index(drop=True)


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
