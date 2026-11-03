"""Daily prices with the 20 / 50-day moving averages and their crossovers (page 1, Live Market Overview).

    GET /api/market/history?symbol=RELIANCE.NS&period=1y              rows, signals and the chart (Plotly JSON)
    GET /api/market/history/download?symbol=RELIANCE.NS&format=csv    the same rows as a CSV or Excel file

Uses the same code as page 1 (marketlens/data.py), and like page 1 it downloads extra history before the
chosen period, so both averages already exist on the first day shown. Historical analysis for learning,
not a live trading recommendation.
"""
import pandas as pd
from fastapi import APIRouter, Query
from fastapi.responses import Response

from marketlens.api.errors import ApiError
from marketlens.api.schemas import df_to_records, fig_to_json
from marketlens.data import add_moving_averages, crossover_chart, download_history
from marketlens.export import to_csv_bytes, to_excel_bytes

router = APIRouter(prefix="/api/market", tags=["market"])
PERIODS = ("1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max")
# Period shown -> (period downloaded, months kept); the extra history warms up the 50-day average
EXTRA = {"1mo": ("6mo", 1), "3mo": ("1y", 3), "6mo": ("2y", 6), "1y": ("2y", 12), "2y": ("5y", 24),
         "5y": ("10y", 60), "10y": ("max", 120), "max": ("max", None)}
COLUMNS = ["date", "open", "high", "low", "close", "volume", "ma20", "ma50", "signal"]
FORMATS = {"csv": ("text/csv", "csv"),
           "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")}
NOTE = "Historical analysis for learning, not a live trading recommendation."


# The checked symbol and period, or a friendly 400.
def _check(symbol: str, period: str) -> tuple[str, str]:
    symbol, period = symbol.strip().upper(), period.strip().lower()
    if period not in PERIODS:
        raise ApiError(f"period must be one of {', '.join(PERIODS)} (not '{period}')")
    return symbol, period


# Prices of the chosen period with MA20, MA50 and Signal (date index), or a friendly 400.
def history_frame(symbol: str, period: str) -> pd.DataFrame:
    fetch, months = EXTRA[period]
    prices = download_history(symbol, period=fetch)
    if prices.empty:
        raise ApiError(f"No prices found for {symbol} on Yahoo Finance. NSE shares end in .NS (e.g. RELIANCE.NS), "
                       "indices start with ^ (e.g. ^NSEI).")
    table = add_moving_averages(prices, 20, 50)
    if months:
        table = table[table.index >= table.index[-1] - pd.DateOffset(months=months)]
    return table


# The table with our short column names and plain dates.
def _rows(table: pd.DataFrame) -> pd.DataFrame:
    out = table.copy()
    out.index.name = "date"
    out = out.reset_index().rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                                            "Volume": "volume", "MA20": "ma20", "MA50": "ma50", "Signal": "signal"})
    out["date"] = pd.to_datetime(out["date"]).dt.date
    prices = ["open", "high", "low", "close", "ma20", "ma50"]
    out[prices] = out[prices].round(2)                     # paise; Yahoo's adjusted prices have 13 decimals
    return out[COLUMNS]


# Daily OHLC, volume, MA20, MA50, the crossover signals and the chart for one Yahoo Finance symbol.
@router.get("/history")
def history(symbol: str = Query(..., min_length=1, max_length=30, description="Yahoo symbol, e.g. RELIANCE.NS or ^NSEI"),
            period: str = Query("1y", description=f"one of {', '.join(PERIODS)}")) -> dict:
    """Prices and signals: signal +1 = MA20 crossed above MA50 (bullish), -1 = crossed below (bearish)."""
    symbol, period = _check(symbol, period)
    table = history_frame(symbol, period)
    rows = _rows(table)
    crosses = rows[rows["signal"] != 0]
    signals = [{"date": r["date"], "type": "bullish" if r["signal"] > 0 else "bearish", "close": r["close"],
                "ma20": r["ma20"], "ma50": r["ma50"]}
               for r in df_to_records(crosses[["date", "signal", "close", "ma20", "ma50"]])]
    return {"symbol": symbol, "period": period, "count": len(rows), "rows": df_to_records(rows), "signals": signals,
            "chart": fig_to_json(crossover_chart(table, f"{symbol}: 20 / 50-day moving averages")), "note": NOTE}


# The same rows as a file to save: CSV or Excel.
@router.get("/history/download")
def history_download(symbol: str = Query(..., min_length=1, max_length=30), period: str = Query("1y"),
                     format: str = Query("csv", description="csv or xlsx")) -> Response:
    """A file with date, OHLC, volume, MA20, MA50 and signal."""
    symbol, period = _check(symbol, period)
    kind = format.strip().lower()
    if kind not in FORMATS:
        raise ApiError("format must be csv or xlsx")
    rows = _rows(history_frame(symbol, period))
    media, extension = FORMATS[kind]
    body = to_csv_bytes(rows) if kind == "csv" else to_excel_bytes({"Prices": rows})
    name = f"{symbol.replace('^', '')}_{period}_prices.{extension}"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})
