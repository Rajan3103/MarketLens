"""Tests for marketlens/data.py. No internet needed: we use made-up prices."""
import pandas as pd
import pytest

from marketlens.data import add_moving_averages

# Prices fall, then rise. With a 2-day and 3-day average the fast one
# crosses ABOVE the slow one on day 6 (a bullish cross).
#   day:    0   1    2    3    4    5     6    7    8
#   close: 10   9    8    7    6    7     8    9   10
#   MA2:   -  9.5  8.5  7.5  6.5  6.5   7.5  8.5  9.5
#   MA3:   -   -   9.0  8.0  7.0  6.67  7.0  8.0  9.0
CLOSES = [10, 9, 8, 7, 6, 7, 8, 9, 10]
CROSS_DAY = 6


# Build a small price table with one row per day, like yfinance returns.
@pytest.fixture
def prices() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(CLOSES), freq="D")
    return pd.DataFrame({"Close": CLOSES}, index=dates, dtype=float)


# The function should add one column per average, named MA<days>.
def test_adds_moving_average_columns(prices):
    df = add_moving_averages(prices, fast=2, slow=3)
    assert "MA2" in df.columns
    assert "MA3" in df.columns
    assert "Signal" in df.columns


# Check the average values against the numbers worked out by hand above.
def test_moving_average_values(prices):
    df = add_moving_averages(prices, fast=2, slow=3)
    # Not enough days yet, so the first values are empty (NaN)
    assert pd.isna(df["MA2"].iloc[0])
    assert pd.isna(df["MA3"].iloc[1])
    assert df["MA2"].iloc[1] == pytest.approx(9.5)
    assert df["MA3"].iloc[2] == pytest.approx(9.0)
    assert df["MA3"].iloc[5] == pytest.approx(20 / 3)


# A bullish cross gives Signal = 1 on that day, and 0 on every other day.
def test_bullish_cross_gives_signal_1(prices):
    df = add_moving_averages(prices, fast=2, slow=3)
    assert df["Signal"].iloc[CROSS_DAY] == 1
    others = df["Signal"].drop(df.index[CROSS_DAY])
    assert (others == 0).all()


# A bearish cross (fast falls BELOW slow) gives Signal = -1.
def test_bearish_cross_gives_signal_minus_1():
    # Same idea flipped: prices rise, then fall
    closes = [6, 7, 8, 9, 10, 9, 8, 7, 6]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    df = add_moving_averages(pd.DataFrame({"Close": closes}, index=dates, dtype=float), 2, 3)
    assert df["Signal"].iloc[6] == -1


# The function must return a new table and leave the original untouched.
def test_does_not_change_input(prices):
    before = prices.copy()
    add_moving_averages(prices, fast=2, slow=3)
    pd.testing.assert_frame_equal(prices, before)
