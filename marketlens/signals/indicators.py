"""Technical indicators, written to match TradingView's built-in formulas.

Every function takes pandas Series (one value per candle) and returns a Series or a
DataFrame with the same index, so results line up with the price table. The first
few values are empty (NaN) until there are enough candles.

Used by the Market Overview chart (module 1) and the Signals & Indicator Strategy
Builder (module 7), so a rule tested here gives the same signal as in Pine Script.

    rsi(close, 14)                      -> 0..100
    bollinger(close, 20, 2)             -> columns mid, upper, lower
    supertrend(high, low, close, 10, 3) -> columns supertrend, direction (+1 up, -1 down)
"""
import numpy as np
import pandas as pd


# Simple moving average: the plain average of the last n values (TradingView ta.sma).
def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


# Exponential moving average: recent values count more (TradingView ta.ema).
def ema(series: pd.Series, n: int) -> pd.Series:
    """Weight 2/(n+1); like TradingView, it starts from the SMA of the first n values."""
    values = series.astype(float).to_numpy()
    out = np.full(len(values), np.nan)
    alpha = 2 / (n + 1)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) >= n:
        first = valid[0] + n - 1
        out[first] = values[valid[0]:first + 1].mean()
        for i in range(first + 1, len(values)):
            out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=series.index)


# Wilder's moving average (RMA), used inside RSI and ATR (TradingView ta.rma).
def rma(series: pd.Series, n: int) -> pd.Series:
    """Like EMA but with weight 1/n; starts from the SMA of the first n values."""
    values = series.astype(float).to_numpy()
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) >= n:
        first = valid[0] + n - 1
        out[first] = values[valid[0]:first + 1].mean()
        for i in range(first + 1, len(values)):
            out[i] = (values[i] + (n - 1) * out[i - 1]) / n
    return pd.Series(out, index=series.index)


# Relative Strength Index: above 70 often called overbought, below 30 oversold (ta.rsi).
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """100 - 100 / (1 + average gain / average loss), with Wilder's smoothing."""
    change = close.diff()
    gain = rma(change.clip(lower=0), n)
    loss = rma(-change.clip(upper=0), n)
    out = 100 - 100 / (1 + gain / loss)
    out[(loss == 0) & (gain > 0)] = 100.0   # only rises
    out[(gain == 0) & (loss > 0)] = 0.0     # only falls
    return out


# True range: the candle's full move including any gap from the previous close.
def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    ranges = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1)
    return ranges.max(axis=1, skipna=False).fillna(high - low)


# Average True Range: how much the price usually moves per candle (ta.atr).
def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), n)


# Bollinger Bands: average +/- k standard deviations (ta.bb).
def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Uses the population standard deviation, like TradingView."""
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * std, "lower": mid - k * std})


# MACD: fast EMA minus slow EMA, its signal line and the histogram (ta.macd).
def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


# Supertrend: a trailing line below price in an uptrend, above it in a downtrend (ta.supertrend).
def supertrend(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 10,
               multiplier: float = 3.0) -> pd.DataFrame:
    """direction = +1 while the trend is up (line below price), -1 while down (line above).
    The line only moves in the trend's favour and flips when the close crosses it."""
    mid = ((high + low) / 2).to_numpy()
    band = (multiplier * atr(high, low, close, n)).to_numpy()
    c = close.to_numpy()
    size = len(c)
    upper, lower = mid + band, mid - band
    line, direction = np.full(size, np.nan), np.full(size, np.nan)
    for i in range(size):
        if np.isnan(band[i]):
            continue
        if i > 0 and not np.isnan(line[i - 1]):
            # Keep the bands from moving against the trend
            if not (upper[i] < upper[i - 1] or c[i - 1] > upper[i - 1]):
                upper[i] = upper[i - 1]
            if not (lower[i] > lower[i - 1] or c[i - 1] < lower[i - 1]):
                lower[i] = lower[i - 1]
            if direction[i - 1] == 1:
                direction[i] = -1 if c[i] < lower[i] else 1
            else:
                direction[i] = 1 if c[i] > upper[i] else -1
        else:
            direction[i] = 1 if c[i] >= mid[i] else -1
        line[i] = lower[i] if direction[i] == 1 else upper[i]
    return pd.DataFrame({"supertrend": line, "direction": direction}, index=close.index)


# Volume-weighted average price, restarting every trading day (ta.vwap).
def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Running total of (typical price x volume) / running total of volume, per day.
    The index must be dates/times; typical price = (high + low + close) / 3."""
    typical = (high + low + close) / 3
    day = pd.Series(pd.DatetimeIndex(close.index).date, index=close.index)
    pv = (typical * volume).groupby(day).cumsum()
    vol = volume.groupby(day).cumsum()
    return (pv / vol.replace(0, np.nan)).rename("vwap")
