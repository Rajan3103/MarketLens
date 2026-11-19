"""Downloads historical prices with yfinance.

NSE tickers on Yahoo Finance end in ".NS", for example RELIANCE.NS.
Indices start with "^", for example ^NSEI (Nifty 50).
"""
import pandas as pd
import yfinance as yf

from marketlens.utils import TtlCache

# Friendly names for the main indices
INDICES = {
    "Nifty 50": "^NSEI",
    "Bank Nifty": "^NSEBANK",
    "India VIX": "^INDIAVIX",
}


# Download price history for one ticker (no cache; the API uses this one).
def download_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """Download price history for one ticker.

    Returns a table with Open, High, Low, Close and Volume columns,
    or an empty table if nothing was found.
    """
    df = yf.download(ticker, period=period, interval=interval,
                     auto_adjust=True, progress=False)
    if df.empty:
        return df
    # yfinance sometimes returns two header rows; keep only the first one
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()


# Remembered downloads (no Streamlit needed, so the API can import this file too). Tests clear it.
CACHE = TtlCache()
SHORT_SECONDS = 60          # recent prices: kept for a minute
LONG_SECONDS = 3600         # a fixed date range: kept for an hour


# The same download, kept for 60 seconds so pages load fast. Each caller gets its own copy of the table.
def get_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    key = ("history", ticker, period, interval)
    return CACHE.get(key, SHORT_SECONDS, lambda: download_history(ticker, period, interval)).copy()


# Daily prices between two dates, kept for an hour.
def get_history_range(ticker: str, start, end) -> pd.DataFrame:
    """Download daily prices between two dates."""
    key = ("range", ticker, str(start), str(end))
    return CACHE.get(key, LONG_SECONDS, lambda: _download_range(ticker, start, end)).copy()


# The real download behind get_history_range.
def _download_range(ticker: str, start, end) -> pd.DataFrame:
    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()


def add_moving_averages(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> pd.DataFrame:
    """Add fast and slow moving averages and mark crossover signals.

    Signal = +1 when the fast average crosses ABOVE the slow one (bullish),
             -1 when it crosses BELOW (bearish), 0 otherwise.
    These are historical signals only, not trading advice.
    """
    df = df.copy()
    df[f"MA{fast}"] = df["Close"].rolling(fast).mean()
    df[f"MA{slow}"] = df["Close"].rolling(slow).mean()
    # 1 when fast is above slow, 0 when below, empty until both averages exist
    above = (df[f"MA{fast}"] > df[f"MA{slow}"]).astype(float)
    above[df[f"MA{slow}"].isna()] = float("nan")
    # A change from 0 to 1 is a bullish cross, from 1 to 0 a bearish cross
    df["Signal"] = above.diff().fillna(0).astype(int)
    return df
