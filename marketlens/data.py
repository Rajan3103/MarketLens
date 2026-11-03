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


# The same download for the Streamlit pages, kept for 60 seconds so pages load fast.
get_history = st.cache_data(ttl=60)(download_history)


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


# Candlestick chart with the fast / slow moving averages and the crossover markers (page 1 and the API).
def crossover_chart(df: pd.DataFrame, title: str = "", fast: int = 20, slow: int = 50):
    """df: the output of add_moving_averages (date index, Open/High/Low/Close, MA columns, Signal)."""
    import plotly.graph_objects as go
    buys, sells = df[df["Signal"] == 1], df[df["Signal"] == -1]
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
                                 name="Price"))
    fig.add_trace(go.Scatter(x=df.index, y=df[f"MA{fast}"], name=f"{fast}-day MA", line=dict(color="#2a78d6")))
    fig.add_trace(go.Scatter(x=df.index, y=df[f"MA{slow}"], name=f"{slow}-day MA", line=dict(color="#eb6834")))
    fig.add_trace(go.Scatter(x=buys.index, y=buys["Close"], mode="markers", name="Buy (bullish cross)",
                             marker=dict(symbol="triangle-up", size=13, color="#16a34a")))
    fig.add_trace(go.Scatter(x=sells.index, y=sells["Close"], mode="markers", name="Sell (bearish cross)",
                             marker=dict(symbol="triangle-down", size=13, color="#dc2626")))
    fig.update_layout(title=title or None, height=520, xaxis_rangeslider_visible=False, hovermode="x unified",
                      legend=dict(orientation="h", y=1.08), margin=dict(l=10, r=10, t=60, b=10),
                      yaxis_title="Price (Rs)")
    return fig

