"""GET /api/market/history?symbol=RELIANCE.NS&period=1y: daily prices with 20 / 50-day averages and crossovers.

Uses the same code as page 1 (marketlens/data.py: download_history + add_moving_averages), so the numbers
match the dashboard. Historical analysis for learning, not a live trading recommendation.
"""
from fastapi import APIRouter, Query

from marketlens.api.errors import ApiError
from marketlens.api.schemas import df_to_records
from marketlens.data import add_moving_averages, download_history

router = APIRouter(prefix="/api/market", tags=["market"])
PERIODS = ("1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max")
NOTE = "Historical analysis for learning, not a live trading recommendation."


# Daily OHLC, volume, MA20, MA50 and the crossover signals for one Yahoo Finance symbol.
@router.get("/history")
def history(symbol: str = Query(..., min_length=1, max_length=30, description="Yahoo symbol, e.g. RELIANCE.NS or ^NSEI"),
            period: str = Query("1y", description=f"one of {', '.join(PERIODS)}")) -> dict:
    """Prices and signals: signal +1 = MA20 crossed above MA50 (bullish), -1 = crossed below (bearish)."""
    symbol, period = symbol.strip().upper(), period.strip().lower()
    if period not in PERIODS:
        raise ApiError(f"period must be one of {', '.join(PERIODS)} (not '{period}')")
    prices = download_history(symbol, period=period)
    if prices.empty:
        raise ApiError(f"No prices found for {symbol} on Yahoo Finance. NSE shares end in .NS (e.g. RELIANCE.NS), "
                       "indices start with ^ (e.g. ^NSEI).")
    table = add_moving_averages(prices, 20, 50)
    table.index.name = "date"
    table = table.reset_index().rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                                                "Volume": "volume", "MA20": "ma20", "MA50": "ma50",
                                                "Signal": "signal"})
    table["date"] = table["date"].dt.date
    rows = df_to_records(table[["date", "open", "high", "low", "close", "volume", "ma20", "ma50", "signal"]])
    crosses = table[table["signal"] != 0]
    signals = [{"date": r["date"], "type": "bullish" if r["signal"] > 0 else "bearish", "close": r["close"]}
               for r in df_to_records(crosses[["date", "signal", "close"]])]
    return {"symbol": symbol, "period": period, "count": len(rows), "rows": rows, "signals": signals, "note": NOTE}
