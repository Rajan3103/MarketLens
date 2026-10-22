"""Downloads historical prices with yfinance.

NSE tickers on Yahoo Finance end in ".NS", for example RELIANCE.NS.
Indices start with "^", for example ^NSEI (Nifty 50).
"""
import pandas as pd
import streamlit as st
import yfinance as yf

# Friendly names for the main indices
INDICES = {
    "Nifty 50": "^NSEI",
    "Bank Nifty": "^NSEBANK",
    "India VIX": "^INDIAVIX",
}


@st.cache_data(ttl=60)  # keep the result for 60 seconds so pages load fast
def get_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
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


@st.cache_data(ttl=3600)
def get_history_range(ticker: str, start, end) -> pd.DataFrame:
    """Download daily prices between two dates."""
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
