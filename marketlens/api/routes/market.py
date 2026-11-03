"""Live Market Overview (page 1): index cards, Nifty 50 movers and breadth, sector heatmap, price chart.

    GET /api/market/snapshot                      index cards, top 5 gainers / losers, breadth (background job)
    GET /api/market/sectors?window=1 day          sector heatmap: table and chart
    GET /api/market/history?symbol=RELIANCE.NS&period=1y&fast=20&slow=50&ma200=true&bollinger=true
                                                  prices, averages, RSI, Bollinger, crossovers and the chart
    GET /api/market/history/download?...&format=csv    the same rows as a CSV or Excel file

The maths lives in marketlens/overview.py, shared with the Streamlit page. Fetching the Nifty 50 snapshot
takes about 8 seconds, so it runs as a background job: /snapshot answers at once with the last snapshot
(if any) and a job_id to follow while a new one is fetched. Answers are kept for a short time like the
Streamlit page did: snapshot 55 s, sectors 5 minutes, price history 60 s.
Historical analysis for learning, not a live trading recommendation.
"""
import threading

import pandas as pd
from fastapi import APIRouter, Query
from fastapi.responses import Response

from marketlens.api.errors import ApiError
from marketlens.api.jobs import get_job, start_job
from marketlens.api.schemas import df_to_records, fig_to_json
from marketlens.data import download_history
from marketlens.export import to_csv_bytes, to_excel_bytes
from marketlens.logger import get_logger
from marketlens.overview import (SOURCE_YF, WINDOWS, breadth_chart, get_snapshot, has_no_volume, indicator_chart,
                                 indicator_table, market_is_open, market_status, sector_changes, sector_chart,
                                 sector_history, summarise)
from marketlens.utils import TtlCache

log = get_logger(__name__)
router = APIRouter(prefix="/api/market", tags=["market"])
PERIODS = ("1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max")
# Period shown -> (period downloaded, months kept); the extra history warms up the averages and RSI
EXTRA = {"1mo": ("6mo", 1), "3mo": ("1y", 3), "6mo": ("2y", 6), "1y": ("2y", 12), "2y": ("5y", 24),
         "5y": ("10y", 60), "10y": ("max", 120), "max": ("max", None)}
COLUMNS = ["date", "open", "high", "low", "close", "volume", "ma_fast", "ma_slow", "ma200", "rsi", "bb_upper",
           "bb_lower", "signal"]
FORMATS = {"csv": ("text/csv", "csv"),
           "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")}
NOTE = "Historical analysis for learning, not a live trading recommendation."
SNAPSHOT_SECONDS = 55           # the page asks every 60 s in market hours
SECTOR_SECONDS = 300
HISTORY_SECONDS = 60
CACHE = TtlCache()
_refresh = {"job_id": None}     # the snapshot job running now (one at a time for everybody)
_refresh_lock = threading.Lock()


# ---------------------------------------------------------------- price chart

# The checked symbol and period, or a friendly 400.
def _check(symbol: str, period: str) -> tuple[str, str]:
    symbol, period = symbol.strip().upper(), period.strip().lower()
    if period not in PERIODS:
        raise ApiError(f"period must be one of {', '.join(PERIODS)} (not '{period}')")
    return symbol, period


# Prices of the chosen period with the averages, RSI, Bollinger Bands and Signal (date index), or a friendly 400.
def history_frame(symbol: str, period: str, fast: int = 20, slow: int = 50) -> pd.DataFrame:
    fetch, months = EXTRA[period]
    prices = CACHE.get(("history", symbol, fetch), HISTORY_SECONDS, lambda: download_history(symbol, period=fetch))
    if prices.empty:
        raise ApiError(f"No prices found for {symbol} on Yahoo Finance. NSE shares end in .NS (e.g. RELIANCE.NS), "
                       "indices start with ^ (e.g. ^NSEI).")
    return indicator_table(prices, fast, slow, months)


# The table with our short column names and plain dates, prices rounded to paise.
def _rows(table: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    out = table.copy()
    out.index.name = "date"
    out = out.reset_index().rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume", f"MA{fast}": "ma_fast",
        f"MA{slow}": "ma_slow", "MA200": "ma200", "RSI": "rsi", "upper": "bb_upper", "lower": "bb_lower",
        "Signal": "signal"})
    if "ma_slow" not in out:                     # slow = 200: MA200 and the slow average are the same column
        out["ma_slow"] = out["ma200"]
    out["date"] = pd.to_datetime(out["date"]).dt.date
    numbers = ["open", "high", "low", "close", "ma_fast", "ma_slow", "ma200", "rsi", "bb_upper", "bb_lower"]
    out[numbers] = out[numbers].round(2)                   # Yahoo's adjusted prices have 13 decimals
    return out[COLUMNS]


# Daily candles with the chosen averages, crossovers, MA200, RSI(14), Bollinger Bands and the 3-panel chart.
@router.get("/history")
def history(symbol: str = Query(..., min_length=1, max_length=30, description="Yahoo symbol, e.g. RELIANCE.NS or ^NSEI"),
            period: str = Query("1y", description=f"one of {', '.join(PERIODS)}"),
            fast: int = Query(20, ge=5, le=200, description="fast moving average, days"),
            slow: int = Query(50, ge=5, le=200, description="slow moving average, days"),
            ma200: bool = Query(False, description="draw the 200-day average"),
            bollinger: bool = Query(False, description="draw Bollinger Bands (20, 2)")) -> dict:
    """Prices and signals: signal +1 = fast average crossed above the slow one (bullish), -1 = crossed below."""
    symbol, period = _check(symbol, period)
    table = history_frame(symbol, period, fast, slow)
    rows = _rows(table, fast, slow)
    crosses = rows[rows["signal"] != 0]
    signals = [{"date": r["date"], "type": "bullish" if r["signal"] > 0 else "bearish", "close": r["close"],
                "ma_fast": r["ma_fast"], "ma_slow": r["ma_slow"]}
               for r in df_to_records(crosses[["date", "signal", "close", "ma_fast", "ma_slow"]])]
    chart = indicator_chart(table, fast, slow, show_ma200=ma200, show_bb=bollinger,
                            title=f"{symbol}: {fast} / {slow}-day moving averages")
    return {"symbol": symbol, "period": period, "fast": fast, "slow": slow, "count": len(rows),
            "rows": df_to_records(rows), "signals": signals, "no_volume": has_no_volume(table),
            "chart": fig_to_json(chart), "note": NOTE}


# The same rows as a file to save: CSV or Excel (columns named after the chosen averages, e.g. ma20, ma50).
@router.get("/history/download")
def history_download(symbol: str = Query(..., min_length=1, max_length=30), period: str = Query("1y"),
                     fast: int = Query(20, ge=5, le=200), slow: int = Query(50, ge=5, le=200),
                     format: str = Query("csv", description="csv or xlsx")) -> Response:
    """A file with date, OHLC, volume, both averages, MA200, RSI 14, Bollinger Bands and signal."""
    symbol, period = _check(symbol, period)
    kind = format.strip().lower()
    if kind not in FORMATS:
        raise ApiError("format must be csv or xlsx")
    rows = _rows(history_frame(symbol, period, fast, slow), fast, slow)
    if 200 in (fast, slow):
        rows = rows.drop(columns="ma200")
    rows = rows.rename(columns={"ma_fast": f"ma{fast}", "ma_slow": f"ma{slow}", "rsi": "rsi14"})
    media, extension = FORMATS[kind]
    body = to_csv_bytes(rows) if kind == "csv" else to_excel_bytes({"Prices": rows})
    name = f"{symbol.replace('^', '')}_{period}_prices.{extension}"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- snapshot (index cards, movers, breadth)

# A table of prices rounded for the page.
def _priced(df: pd.DataFrame, columns: list[str]) -> list[dict]:
    out = df[columns].copy()
    for col in ("last", "prev_close", "change", "pct_change"):
        if col in out:
            out[col] = out[col].astype(float).round(2)
    return df_to_records(out)


# One snapshot as JSON for the page.
def snapshot_json(snap) -> dict:
    s = summarise(snap)
    cards = _priced(s["indices"], ["symbol", "name", "last", "prev_close", "change", "pct_change"])
    for card in cards:
        card["inverse"] = card["symbol"] == "INDIAVIX"       # VIX going up is bad news: show its rise in red
    movers = ["symbol", "name", "last", "change", "pct_change"]
    return {"indices": cards, "gainers": _priced(s["gainers"], movers), "losers": _priced(s["losers"], movers),
            "breadth": s["breadth"], "breadth_chart": fig_to_json(breadth_chart(s["breadth"])),
            "stock_count": s["stock_count"], "source": s["source"], "live": s["live"],
            "as_of": s["as_of"].isoformat(), "as_of_text": f"{s['as_of']:%d-%b-%Y %H:%M:%S} IST",
            "key_note": "Add the Dhan keys in .env for live prices." if s["source"] == SOURCE_YF else None}


# The background work: fetch a new snapshot and keep it for the next 55 seconds.
def refresh_snapshot(job) -> dict:
    job.progress(5, "Fetching the 5 indices and the 50 Nifty stocks")
    data = snapshot_json(get_snapshot())
    CACHE.put("snapshot", data)
    return {"stocks": data["stock_count"], "source": data["source"]}


# The job fetching a snapshot now: the running one if there is one, otherwise a new one (None = no database).
def _start_refresh() -> str | None:
    with _refresh_lock:
        running = _refresh["job_id"]
        if running:
            job = get_job(running)
            if job and not job["finished"]:
                return running
        try:
            _refresh["job_id"] = start_job("market_snapshot", refresh_snapshot)
        except Exception as err:          # no database for the jobs table: fetch it here instead (slower)
            log.warning("Snapshot job could not start (%s); fetching in the request", err)
            _refresh["job_id"] = None
            CACHE.put("snapshot", snapshot_json(get_snapshot()))
        return _refresh["job_id"]


# Index cards, Nifty 50 movers and breadth. When older than 55 s a job fetches a new one; follow job_id.
@router.get("/snapshot")
def snapshot() -> dict:
    """{snapshot (the last one, or null the first time), age_seconds, job_id (null when fresh), open, status}."""
    found = CACHE.peek("snapshot", SNAPSHOT_SECONDS)
    job_id = None
    if found is None:
        job_id = _start_refresh()
        found = CACHE.peek("snapshot")
    return {"snapshot": found[1] if found else None, "age_seconds": round(found[0]) if found else None,
            "job_id": job_id, "open": market_is_open(), "status": market_status()}


# ---------------------------------------------------------------- sector heatmap

# % change of each Nifty sector index over 1 day, 1 week or 1 month, with the treemap.
@router.get("/sectors")
def sectors(window: str = Query("1 day", description=f"one of {', '.join(WINDOWS)}")) -> dict:
    if window not in WINDOWS:
        raise ApiError(f"window must be one of {', '.join(WINDOWS)} (not '{window}')")
    table, skipped = sector_changes(CACHE.get("sectors", SECTOR_SECONDS, sector_history), window)
    rows = table.assign(last=table["last"].round(2), pct_change=table["pct_change"].round(2))
    note = f"Nifty sector indices from yfinance, {window} change."
    if skipped:
        note += f" Skipped (not enough data on yfinance): {', '.join(skipped)}."
    return {"window": window, "windows": list(WINDOWS), "rows": df_to_records(rows), "skipped": skipped,
            "chart": fig_to_json(sector_chart(table)) if len(table) else None, "note": note}
