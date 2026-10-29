"""Tests for marketlens/backtest/metrics.py, with small numbers worked out by hand."""
import math

import pandas as pd
import pytest

from marketlens.backtest.metrics import (average_win_loss, cagr, calmar, compute_metrics, daily_returns,
                                         drawdown, equity_curve, expectancy, max_drawdown, profit_factor,
                                         sharpe, sortino, streaks, total_pnl, win_rate)

# Six trades: win, win, loss, loss, zero (counts as a loss), win
PNLS = [100, 200, -50, -25, 0, 75]


# Total 300; 3 of 6 win; wins average 125, losses (-50 -25 +0) / 3 = -25.
def test_trade_numbers():
    assert total_pnl(PNLS) == 300 and total_pnl([]) == 0
    assert win_rate(PNLS) == 50 and win_rate([]) is None
    assert average_win_loss(PNLS) == (125, -25)
    assert profit_factor(PNLS) == 375 / 75                 # money won / money lost = 5
    assert expectancy(PNLS) == 50                          # 300 / 6 trades
    assert streaks(PNLS) == (2, 3)                         # W W L L L W


# No losing trade: profit factor cannot be worked out.
def test_no_losses():
    assert profit_factor([10, 20]) is None and average_win_loss([10, 20]) == (15, None)
    assert streaks([]) == (0, 0) and expectancy([]) is None


# 1,00,000 -> 1,21,000 in two years = 10% a year.
def test_cagr():
    assert cagr(100_000, 121_000, 730) == pytest.approx(10.0)
    assert cagr(100_000, 100_000, 365) == 0
    assert cagr(100_000, -5_000, 365) == -100.0 and cagr(100_000, 110_000, 0) is None


# Capital 1000, days +100 -200 +50 +300 -> 1100, 900, 950, 1250; worst = 200 below 1100.
def test_drawdown():
    daily = pd.Series([100, -200, 50, 300], index=pd.date_range("2026-01-01", periods=4).date)
    equity = equity_curve(daily, 1000)
    assert list(equity) == [1100, 900, 950, 1250]
    dd = drawdown(equity, 1000)
    assert list(dd["dd_rs"]) == [0, -200, -150, 0]
    assert max_drawdown(equity, 1000) == pytest.approx((-200, -200 / 1100 * 100))


# A loss on day one is a drawdown from the starting capital.
def test_drawdown_from_capital():
    equity = equity_curve(pd.Series([-100.0], index=[pd.Timestamp("2026-01-01").date()]), 1000)
    assert max_drawdown(equity, 1000) == (-100, -10)
    assert max_drawdown(pd.Series(dtype=float), 1000) == (0, 0)


# Day 1: 100 on 1000 = 10%; day 2: -110 on 1100 = -10%.
def test_daily_returns():
    daily = pd.Series([100.0, -110.0], index=pd.date_range("2026-01-01", periods=2).date)
    assert list(daily_returns(daily, 1000)) == pytest.approx([0.10, -0.10])


# Returns 1%, -1%, 2%, 0%: mean 0.5%, std sqrt(0.0005/3); downside sqrt(0.0001/4) = 0.5%.
def test_sharpe_sortino():
    r = pd.Series([0.01, -0.01, 0.02, 0.0])
    assert sharpe(r) == pytest.approx(0.005 / math.sqrt(0.0005 / 3) * math.sqrt(252))
    assert sortino(r) == pytest.approx(0.005 / 0.005 * math.sqrt(252))
    assert sharpe(r, risk_free_pct=6.3) < sharpe(r)          # a risk-free rate lowers it
    assert sharpe(pd.Series([0.01])) is None and sharpe(pd.Series([0.01, 0.01])) is None
    assert sortino(pd.Series([0.01, 0.02])) is None          # no losing day


# Calmar = CAGR / |max drawdown %|.
def test_calmar():
    assert calmar(20, -10) == 2 and calmar(None, -10) is None and calmar(20, 0) is None


# Everything together for a tiny backtest.
def test_compute_metrics():
    days = pd.date_range("2026-01-01", periods=4).date
    daily = pd.Series([100.0, -200, 50, 300], index=days)
    m = compute_metrics([100, -200, 50, 300], daily, 1000, days[0], days[-1])
    assert m["total_pnl"] == 250 and m["return_pct"] == 25 and m["trades"] == 4
    assert m["max_drawdown_rs"] == -200 and m["win_rate_pct"] == 75
    assert m["best_day"] == 300 and m["worst_day"] == -200 and m["trading_days"] == 4
    assert m["cagr_pct"] == pytest.approx((1.25 ** (365 / 3) - 1) * 100)
    empty = compute_metrics([], pd.Series(dtype=float), 1000)
    assert empty["total_pnl"] == 0 and empty["cagr_pct"] is None and empty["win_rate_pct"] is None
