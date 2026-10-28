"""Tests for marketlens/signals/indicators.py.

Each result is checked against numbers worked out by hand or against a slow,
obvious loop written here, so the fast versions can be trusted.
"""
import numpy as np
import pandas as pd
import pytest

from marketlens.signals.indicators import (atr, bollinger, ema, macd, rma, rsi, sma, supertrend,
                                           true_range, vwap)

DAYS = pd.bdate_range("2026-01-01", periods=60)


# A gently wiggling made-up price series (same every run).
@pytest.fixture
def close():
    rng = np.random.default_rng(7)
    return pd.Series(100 + np.cumsum(rng.normal(0, 1, len(DAYS))), index=DAYS)


# SMA of 1..5 over 3 values: 2, 3, 4 (first two empty).
def test_sma():
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert sma(s, 3).tolist()[2:] == [2.0, 3.0, 4.0] and sma(s, 3).isna().sum() == 2


# EMA starts at the SMA of the first n values, then weights 2/(n+1).
def test_ema_by_hand():
    s = pd.Series([1.0, 2, 3, 4, 5])
    out = ema(s, 3)                      # alpha = 0.5
    assert out.iloc[2] == 2.0            # SMA of 1,2,3
    assert out.iloc[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)   # 3.0
    assert out.iloc[4] == pytest.approx(0.5 * 5 + 0.5 * 3.0)   # 4.0


# Wilder's average: weight 1/n.
def test_rma_by_hand():
    out = rma(pd.Series([2.0, 4, 6, 8]), 2)
    assert out.iloc[1] == 3.0 and out.iloc[2] == pytest.approx((6 + 3.0) / 2) and out.iloc[3] == pytest.approx((8 + 4.5) / 2)


# RSI against a slow textbook loop (Wilder's smoothing).
def test_rsi_matches_loop(close):
    n = 14
    change = close.diff().to_numpy()
    gains, losses = np.clip(change, 0, None), np.clip(-change, 0, None)
    avg_g, avg_l = gains[1:n + 1].mean(), losses[1:n + 1].mean()
    expected = {n: 100 - 100 / (1 + avg_g / avg_l)}
    for i in range(n + 1, len(close)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
        expected[i] = 100 - 100 / (1 + avg_g / avg_l)
    got = rsi(close, n)
    assert got.iloc[:n].isna().all()
    for i, value in expected.items():
        assert got.iloc[i] == pytest.approx(value, abs=1e-9)


# Only rises -> 100; only falls -> 0; always between 0 and 100.
def test_rsi_limits(close):
    assert rsi(pd.Series(np.arange(30.0)), 14).iloc[-1] == 100
    assert rsi(pd.Series(np.arange(30.0, 0, -1)), 14).iloc[-1] == 0
    assert rsi(close).dropna().between(0, 100).all()


# True range includes gaps; ATR of a steady 2-point range is 2.
def test_true_range_and_atr():
    high = pd.Series([11.0, 15, 12]); low = pd.Series([9.0, 13, 10]); close_ = pd.Series([10.0, 14, 11])
    assert true_range(high, low, close_).tolist() == [2.0, 5.0, 4.0]   # gap up 10 -> 15
    steady = pd.Series(np.arange(30.0))
    assert atr(steady + 1, steady - 1, steady, 14).iloc[-1] == pytest.approx(2.0, abs=1e-9)


# Bollinger: middle = SMA, bands = middle +/- 2 population standard deviations.
def test_bollinger():
    s = pd.Series([1.0, 2, 3, 4, 5])
    bb = bollinger(s, 5, 2)
    std = np.std([1, 2, 3, 4, 5])        # population std = sqrt(2)
    assert bb["mid"].iloc[-1] == 3.0
    assert bb["upper"].iloc[-1] == pytest.approx(3 + 2 * std) and bb["lower"].iloc[-1] == pytest.approx(3 - 2 * std)


# MACD = EMA12 - EMA26; histogram = MACD - signal.
def test_macd(close):
    m = macd(close)
    assert m["macd"].iloc[-1] == pytest.approx(ema(close, 12).iloc[-1] - ema(close, 26).iloc[-1])
    assert (m["hist"] - (m["macd"] - m["signal"])).abs().max() < 1e-12
    assert m["signal"].first_valid_index() == close.index[25 + 8]   # 26 + 9 - 2 candles


# Supertrend: below price and +1 in a rise, above price and -1 in a fall, flips when the trend turns.
def test_supertrend():
    up = pd.Series(np.linspace(100, 160, 40)); down = pd.Series(np.linspace(160, 100, 40))
    prices = pd.concat([up, down], ignore_index=True)
    st = supertrend(prices + 1, prices - 1, prices, 10, 3)
    rising, falling = st.iloc[15:40], st.iloc[60:]
    assert (rising["direction"] == 1).all() and (rising["supertrend"] < prices.iloc[15:40]).all()
    assert (falling["direction"] == -1).all() and (falling["supertrend"] > prices.iloc[60:]).all()
    # In an uptrend the line never moves down
    assert rising["supertrend"].diff().dropna().ge(0).all()


# VWAP by hand, restarting on a new day.
def test_vwap():
    idx = pd.to_datetime(["2026-10-21 09:15", "2026-10-21 09:16", "2026-10-22 09:15"])
    high = pd.Series([12.0, 14, 30], index=idx); low = pd.Series([8.0, 10, 24], index=idx)
    close_ = pd.Series([10.0, 12, 27], index=idx); volume = pd.Series([100.0, 300, 50], index=idx)
    v = vwap(high, low, close_, volume)
    assert v.iloc[0] == pytest.approx(10.0)
    assert v.iloc[1] == pytest.approx((10 * 100 + 12 * 300) / 400)   # 11.5
    assert v.iloc[2] == pytest.approx(27.0)                         # new day starts again
