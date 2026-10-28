"""Daily equity / ETF prices: download with yfinance, store in PostgreSQL, read back.

The engines (momentum, ETF rotation, backtests) never call the internet
themselves. They call load_prices(), which reads from the database.
Only sync_prices() talks to yfinance, and it downloads just the missing days.

    from marketlens.ingest.prices import sync_prices, load_prices
    sync_prices(["RELIANCE", "TCS"], date(2016, 1, 1), date.today())
    closes = load_prices(["RELIANCE", "TCS"], date(2020, 1, 1), date.today())
"""
import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yfinance as yf
from sqlalchemy import func, select

from marketlens.db import get_session, upsert
from marketlens.logger import get_logger
from marketlens.models import DailyPrice, Instrument
from marketlens.utils import retry

log = get_logger(__name__)

UNIVERSE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "universe"
UNIVERSES = ("nifty50", "nifty200", "etfs")
BATCH_SIZE = 50            # tickers per yfinance request (bigger requests fail more often)
ADJUST_TOLERANCE = 0.005   # 0.5%: bigger change in an old close = split/bonus/dividend adjustment


@dataclass
class SyncSummary:
    """What sync_prices() did, for the log and the screen."""
    symbols: int = 0
    new_instruments: int = 0
    rows_upserted: int = 0
    up_to_date: list = field(default_factory=list)
    refreshed: list = field(default_factory=list)   # history re-downloaded after an adjustment
    failed: list = field(default_factory=list)

    # One line that sums everything up.
    def __str__(self) -> str:
        return (f"{self.symbols} symbols: {self.rows_upserted:,} rows saved, "
                f"{self.new_instruments} new instruments, {len(self.up_to_date)} already up to date, "
                f"{len(self.refreshed)} re-downloaded after adjustment, {len(self.failed)} failed"
                + (f" ({', '.join(self.failed)})" if self.failed else ""))


# Read one universe file (nifty50, nifty200 or etfs) from data/universe/.
def load_universe(name: str) -> pd.DataFrame:
    """Return a table with symbol, name and sector (or category for ETFs)."""
    if name not in UNIVERSES:
        raise ValueError(f"universe must be one of {UNIVERSES}, not {name!r}")
    return pd.read_csv(UNIVERSE_DIR / f"{name}.csv", dtype=str).fillna("")


# NSE symbol -> Yahoo Finance ticker.
def yf_ticker(symbol: str) -> str:
    """RELIANCE -> RELIANCE.NS. Indices (^NSEI) and tickers that already have a suffix stay as they are."""
    symbol = symbol.strip().upper()
    if symbol.startswith("^") or symbol.endswith((".NS", ".BO")):
        return symbol
    return f"{symbol}.NS"


# Decide the first date to download for one instrument.
def fetch_start(last_stored: dt.date | None, start: dt.date) -> dt.date:
    """Nothing stored yet -> start. Otherwise the LAST stored day (not the day after),
    so we can compare that day's close and spot split/dividend adjustments."""
    if last_stored is None or last_stored < start:
        return start
    return last_stored


# Download daily prices for many tickers in one request (tried again on errors).
@retry
def download_prices(tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
    """yfinance's `end` is not included, so we ask for one day more."""
    data = yf.download(tickers, start=start, end=end + dt.timedelta(days=1), auto_adjust=True,
                       group_by="ticker", progress=False, threads=True)
    if data is None or data.empty:
        raise ConnectionError(f"yfinance returned no data for {len(tickers)} ticker(s)")
    return data


# Pull one ticker's Open/High/Low/Close/Volume out of a yfinance download.
def ticker_frame(data: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance gives (ticker, field) columns for many tickers, and sometimes
    (field, ticker) or plain field columns; this handles all three."""
    if isinstance(data.columns, pd.MultiIndex):
        if ticker in data.columns.get_level_values(0):
            frame = data[ticker]
        elif ticker in data.columns.get_level_values(1):
            frame = data.xs(ticker, axis=1, level=1)
        else:
            return pd.DataFrame()
    else:
        frame = data
    needed = ["Open", "High", "Low", "Close"]
    if not set(needed) <= set(frame.columns):
        return pd.DataFrame()
    return frame.dropna(subset=needed)


# Turn a price table into rows for the daily_prices table.
def frame_to_rows(frame: pd.DataFrame, instrument_id: int) -> list[dict]:
    """Prices rounded to 4 decimals; missing volume stored as empty (NULL)."""
    rows = []
    for day, bar in frame.iterrows():
        volume = bar.get("Volume")
        rows.append({
            "instrument_id": instrument_id,
            "date": pd.Timestamp(day).date(),
            "open": round(float(bar["Open"]), 4),
            "high": round(float(bar["High"]), 4),
            "low": round(float(bar["Low"]), 4),
            "close": round(float(bar["Close"]), 4),
            "volume": None if volume is None or math.isnan(volume) else int(volume),
            "source": "yfinance",
        })
    return rows


# Make sure every symbol has a row in instruments; return {symbol: id}.
def ensure_instruments(session, symbols: list[str], instrument_type: str = "EQUITY",
                       names: dict | None = None) -> tuple[dict, int]:
    """Adds the missing ones and returns (ids by symbol, how many were new)."""
    names = names or {}
    existing = dict(session.execute(
        select(Instrument.symbol, Instrument.id).where(Instrument.symbol.in_(symbols),
                                                       Instrument.exchange == "NSE")).all())
    missing = [s for s in symbols if s not in existing]
    if missing:
        upsert(session, Instrument, [
            {"symbol": s, "exchange": "NSE", "instrument_type": instrument_type,
             "name": names.get(s) or None, "yf_ticker": yf_ticker(s)} for s in missing
        ], ["symbol", "exchange"])
        session.commit()
        existing = dict(session.execute(
            select(Instrument.symbol, Instrument.id).where(Instrument.symbol.in_(symbols),
                                                           Instrument.exchange == "NSE")).all())
    return existing, len(missing)


# Find the newest stored date and its close for each instrument.
def last_stored(session, instrument_ids: list[int]) -> dict:
    """Return {instrument_id: (last date, close on that date)}."""
    newest = (select(DailyPrice.instrument_id, func.max(DailyPrice.date).label("d"))
              .where(DailyPrice.instrument_id.in_(instrument_ids))
              .group_by(DailyPrice.instrument_id).subquery())
    rows = session.execute(
        select(DailyPrice.instrument_id, DailyPrice.date, DailyPrice.close)
        .join(newest, (DailyPrice.instrument_id == newest.c.instrument_id)
              & (DailyPrice.date == newest.c.d))).all()
    return {iid: (day, float(close)) for iid, day, close in rows}


# Was the stored close changed by Yahoo (split, bonus or dividend adjustment)?
def history_was_adjusted(stored_close: float, frame: pd.DataFrame, day: dt.date) -> bool:
    """Compare our stored close with the freshly downloaded close for the same day."""
    same_day = frame[[pd.Timestamp(d).date() == day for d in frame.index]]
    if same_day.empty:
        return False
    new_close = float(same_day["Close"].iloc[0])
    return abs(new_close - stored_close) / stored_close > ADJUST_TOLERANCE


# Download one group of tickers and save them; updates the summary.
def _sync_batch(session, symbols: list[str], ids: dict, start: dt.date, end: dt.date,
                stored: dict, history_start: dt.date, summary: SyncSummary) -> None:
    """Shared by sync_prices(); not meant to be called directly."""
    tickers = [yf_ticker(s) for s in symbols]
    try:
        data = download_prices(tickers, start, end)
    except Exception as err:
        log.error("Download failed for %d symbols: %s", len(symbols), err)
        summary.failed.extend(symbols)
        return
    for symbol, ticker in zip(symbols, tickers):
        frame = ticker_frame(data, ticker)
        if frame.empty:
            summary.failed.append(symbol)
            continue
        iid = ids[symbol]
        if iid in stored and history_was_adjusted(stored[iid][1], frame, stored[iid][0]):
            # Old prices changed: download the whole history again for this symbol
            log.info("%s: past prices were adjusted, re-downloading from %s", symbol, history_start)
            try:
                frame = ticker_frame(download_prices([ticker], history_start, end), ticker)
            except Exception as err:
                log.error("%s: full re-download failed: %s", symbol, err)
                summary.failed.append(symbol)
                continue
            summary.refreshed.append(symbol)
        summary.rows_upserted += upsert(session, DailyPrice, frame_to_rows(frame, iid),
                                        ["instrument_id", "date"])
    session.commit()


# Bring the database up to date for these symbols between start and end.
def sync_prices(symbols: list[str], start: dt.date, end: dt.date | None = None,
                instrument_type: str = "EQUITY", names: dict | None = None,
                url: str | None = None, progress=None) -> SyncSummary:
    """1. adds missing instruments   2. finds each one's last stored date
    3. downloads only the missing dates (yfinance, .NS)   4. upserts   5. logs a summary.

    url: another database (the tests use TEST_DATABASE_URL); normally leave it empty.
    progress: optional function(done, total, text) for a progress bar (one step per 50 symbols).
    """
    end = end or dt.date.today()
    symbols = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    summary = SyncSummary(symbols=len(symbols))
    with get_session(url) as session:
        ids, summary.new_instruments = ensure_instruments(session, symbols, instrument_type, names)
        stored = last_stored(session, list(ids.values()))

        # Group symbols by the date their download must start from
        groups: dict[dt.date, list[str]] = {}
        for symbol in symbols:
            last = stored.get(ids[symbol])
            first = fetch_start(last[0] if last else None, start)
            if last and last[0] >= end:
                summary.up_to_date.append(symbol)
                continue
            groups.setdefault(first, []).append(symbol)

        batches = [(first, group[i:i + BATCH_SIZE]) for first, group in sorted(groups.items())
                   for i in range(0, len(group), BATCH_SIZE)]
        for n, (first, batch) in enumerate(batches, start=1):
            _sync_batch(session, batch, ids, first, end, stored, start, summary)
            if progress:
                progress(n, len(batches), f"batch {n} of {len(batches)}")

    log.info("Price sync %s to %s: %s", start, end, summary)
    return summary


# Read close prices from PostgreSQL as a table: one row per date, one column per symbol.
def load_prices(symbols: list[str], start: dt.date, end: dt.date | None = None,
                field_name: str = "close", url: str | None = None) -> pd.DataFrame:
    """Engines use this instead of calling the internet.

    field_name: close (default), open, high, low or volume.
    Symbols with no stored prices are left out (and logged).
    """
    if field_name not in ("open", "high", "low", "close", "volume"):
        raise ValueError("field_name must be open, high, low, close or volume")
    end = end or dt.date.today()
    symbols = [s.strip().upper() for s in symbols]
    column = getattr(DailyPrice, field_name)
    with get_session(url) as session:
        rows = session.execute(
            select(DailyPrice.date, Instrument.symbol, column)
            .join(Instrument, Instrument.id == DailyPrice.instrument_id)
            .where(Instrument.symbol.in_(symbols), DailyPrice.date.between(start, end))
        ).all()
    if not rows:
        log.warning("No stored prices for %s between %s and %s", symbols, start, end)
        return pd.DataFrame()
    table = (pd.DataFrame(rows, columns=["date", "symbol", "value"])
             .pivot(index="date", columns="symbol", values="value").astype(float))
    table.index = pd.to_datetime(table.index)
    missing = [s for s in symbols if s not in table.columns]
    if missing:
        log.warning("No stored prices for: %s", ", ".join(missing))
    return table[[s for s in symbols if s in table.columns]].sort_index()
