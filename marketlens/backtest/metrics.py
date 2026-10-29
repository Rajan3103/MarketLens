"""Backtest results as numbers: P&L, CAGR, drawdown, Sharpe, win rate and more.

Every function takes plain lists or pandas Series, so they are easy to test by hand:
  - trade_pnls: the net P&L (Rs) of each trade, in the order they closed
  - daily_pnl:  a Series of net P&L (Rs) per day, indexed by date
  - capital:    the starting money (Rs)

Ratios that cannot be worked out (no losing trades, a single day, ...) come back as None.
"""
import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252       # trading days in an Indian market year (used to annualise Sharpe / Sortino)


# Turn NaN / infinity into None so they show as "-" and can be saved in the database.
def clean(value):
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) or math.isinf(value) else value


# The sum of all trade P&Ls.
def total_pnl(trade_pnls) -> float:
    return float(np.sum(trade_pnls)) if len(trade_pnls) else 0.0


# Money in the account at the end of every day: capital + P&L so far.
def equity_curve(daily_pnl: pd.Series, capital: float) -> pd.Series:
    return capital + daily_pnl.sort_index().cumsum()


# Compound yearly growth: (end / start) ^ (365 / days) - 1, in percent.
def cagr(start_value: float, end_value: float, days: int):
    """Example: 1,00,000 -> 1,21,000 in 730 days = 10% a year."""
    if days <= 0 or start_value <= 0:
        return None
    if end_value <= 0:
        return -100.0
    return clean(((end_value / start_value) ** (365 / days) - 1) * 100)


# How far below its highest point the account is on each day, in Rs and in percent.
def drawdown(equity: pd.Series, capital: float) -> pd.DataFrame:
    """The starting capital counts as the first high point. Columns: equity, peak, dd_rs, dd_pct."""
    peak = equity.cummax().clip(lower=capital)
    return pd.DataFrame({"equity": equity, "peak": peak, "dd_rs": equity - peak,
                         "dd_pct": (equity - peak) / peak * 100})


# The worst drawdown as (Rs, %); both are 0 or negative.
def max_drawdown(equity: pd.Series, capital: float) -> tuple[float, float]:
    if equity.empty:
        return 0.0, 0.0
    dd = drawdown(equity, capital)
    return float(dd["dd_rs"].min()), float(dd["dd_pct"].min())


# Daily returns: each day's P&L divided by the money at the start of that day.
def daily_returns(daily_pnl: pd.Series, capital: float) -> pd.Series:
    daily_pnl = daily_pnl.sort_index()
    start_of_day = capital + daily_pnl.cumsum().shift(1, fill_value=0.0)
    return daily_pnl / start_of_day


# Sharpe ratio: average daily return / its standard deviation, times sqrt(252).
def sharpe(returns: pd.Series, risk_free_pct: float = 0.0, periods: int = TRADING_DAYS):
    """risk_free_pct: a yearly risk-free rate in percent (0 by default, like most backtest tools)."""
    if len(returns) < 2:
        return None
    excess = returns - risk_free_pct / 100 / periods
    std = excess.std(ddof=1)
    return None if std == 0 else clean(excess.mean() / std * math.sqrt(periods))


# Sortino ratio: like Sharpe, but only the losing days count as "risk".
def sortino(returns: pd.Series, risk_free_pct: float = 0.0, periods: int = TRADING_DAYS):
    """Downside deviation = sqrt(average of (negative returns)^2), counting every day."""
    if len(returns) < 2:
        return None
    excess = returns - risk_free_pct / 100 / periods
    downside = math.sqrt((excess.clip(upper=0) ** 2).mean())
    return None if downside == 0 else clean(excess.mean() / downside * math.sqrt(periods))


# Calmar ratio: CAGR divided by the size of the worst drawdown (both in percent).
def calmar(cagr_pct, max_dd_pct):
    if cagr_pct is None or not max_dd_pct:
        return None
    return clean(cagr_pct / abs(max_dd_pct))


# Percent of trades that made money (a trade at exactly 0 counts as a loss).
def win_rate(trade_pnls):
    pnls = np.asarray(trade_pnls, dtype=float)
    return None if pnls.size == 0 else float((pnls > 0).mean() * 100)


# Average winning trade (Rs) and average losing trade (Rs, negative).
def average_win_loss(trade_pnls) -> tuple:
    pnls = np.asarray(trade_pnls, dtype=float)
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    return (float(wins.mean()) if wins.size else None,
            float(losses.mean()) if losses.size else None)


# Profit factor: money won / money lost. Above 1 means the strategy made money.
def profit_factor(trade_pnls):
    pnls = np.asarray(trade_pnls, dtype=float)
    lost = -pnls[pnls < 0].sum()
    return None if lost == 0 else clean(pnls[pnls > 0].sum() / lost)


# Expectancy: what one trade earns on average = win% x avg win + loss% x avg loss.
def expectancy(trade_pnls):
    pnls = np.asarray(trade_pnls, dtype=float)
    return None if pnls.size == 0 else float(pnls.mean())


# Longest run of winning trades in a row, and of losing trades in a row.
def streaks(trade_pnls) -> tuple[int, int]:
    best_win = best_loss = win = loss = 0
    for pnl in trade_pnls:
        if pnl > 0:
            win, loss = win + 1, 0
        else:
            win, loss = 0, loss + 1
        best_win, best_loss = max(best_win, win), max(best_loss, loss)
    return best_win, best_loss


# Every metric at once, as a dict the pages, Excel report and database all use.
def compute_metrics(trade_pnls, daily_pnl: pd.Series, capital: float,
                    start=None, end=None, risk_free_pct: float = 0.0) -> dict:
    """start / end: the backtest period (dates). Without them the first and last day with P&L are used."""
    daily_pnl = daily_pnl.sort_index()
    equity = equity_curve(daily_pnl, capital)
    total = total_pnl(trade_pnls)
    if start is None or end is None:
        start = daily_pnl.index.min() if len(daily_pnl) else None
        end = daily_pnl.index.max() if len(daily_pnl) else None
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days if start is not None else 0
    growth = cagr(capital, capital + total, days)
    dd_rs, dd_pct = max_drawdown(equity, capital)
    returns = daily_returns(daily_pnl, capital)
    avg_win, avg_loss = average_win_loss(trade_pnls)
    win_streak, loss_streak = streaks(trade_pnls)
    return {
        "total_pnl": total,
        "return_pct": total / capital * 100 if capital else None,
        "cagr_pct": growth,
        "max_drawdown_rs": dd_rs,
        "max_drawdown_pct": dd_pct,
        "sharpe": sharpe(returns, risk_free_pct),
        "sortino": sortino(returns, risk_free_pct),
        "calmar": calmar(growth, dd_pct),
        "trades": len(trade_pnls),
        "win_rate_pct": win_rate(trade_pnls),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor(trade_pnls),
        "expectancy": expectancy(trade_pnls),
        "max_win_streak": win_streak,
        "max_loss_streak": loss_streak,
        "trading_days": int(len(daily_pnl)),
        "best_day": float(daily_pnl.max()) if len(daily_pnl) else None,
        "worst_day": float(daily_pnl.min()) if len(daily_pnl) else None,
    }


# Names shown next to each metric on the page and in the Excel Summary sheet.
METRIC_LABELS = {
    "total_pnl": "Total P&L (Rs)", "return_pct": "Return on capital (%)", "cagr_pct": "CAGR (%)",
    "max_drawdown_rs": "Max drawdown (Rs)", "max_drawdown_pct": "Max drawdown (%)",
    "sharpe": "Sharpe ratio", "sortino": "Sortino ratio", "calmar": "Calmar ratio",
    "trades": "Number of trades", "win_rate_pct": "Win rate (%)", "avg_win": "Average win (Rs)",
    "avg_loss": "Average loss (Rs)", "profit_factor": "Profit factor", "expectancy": "Expectancy per trade (Rs)",
    "max_win_streak": "Max winning streak", "max_loss_streak": "Max losing streak",
    "trading_days": "Days with trades", "best_day": "Best day (Rs)", "worst_day": "Worst day (Rs)",
}
