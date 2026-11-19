"""One backtest result in a shape any screen can draw: the Streamlit page today, the web app tomorrow.

    from marketlens.backtest.present import result_to_json
    result_to_json(result) -> {
        "metrics": [{"key", "label", "value", "unit", "help"}, ...],    # the cards, in order
        "equity": [{"time": "2025-01-01", "value": 500000.0}, ...],     # account value (Rs)
        "drawdown": [{"time", "value" (% below the high), "rs"}, ...],
        "benchmark": [{"time", "value"}, ...] or None,                  # same starting capital
        "yearly_returns": [{"year", "pnl", "return_pct"}, ...],
        "monthly_returns": [{"year", "month", "pnl", "return_pct"}, ...],
        "day_heatmap": <Plotly figure JSON>, "month_heatmap": <Plotly figure JSON>,
        "trades": [ {...one row per trade...} ], "skipped_days": [{"date", "reason"}, ...],
        "historical": True, "note": "Historical analysis for learning, ...", ...}

Time series use {"time", "value"} points, which TradingView Lightweight Charts reads directly; the heatmaps are
Plotly figures. Every backtest route returns this same shape. No Streamlit here.
"""
import pandas as pd

from marketlens.api.schemas import df_to_records, fig_to_json, to_json_value
from marketlens.backtest import charts
from marketlens.backtest.engine import BacktestResult
from marketlens.backtest.metrics import drawdown

NOTE = "Historical analysis for learning, not a live trading recommendation."
MAX_TRADES = 5000                   # rows sent to a screen; the Excel / CSV report always has every trade

# The metric cards in order: (key in result.metrics, label, unit, help). "total_costs" is added up from the trades.
METRICS = [
    ("total_pnl", "Total P&L", "Rs", "Profit or loss after brokerage, taxes and slippage"),
    ("return_pct", "Return on capital", "%", "Total P&L as a percent of the starting capital"),
    ("cagr_pct", "CAGR", "%", "Yearly growth rate if returns compound"),
    ("max_drawdown_rs", "Max drawdown", "Rs", "The biggest fall from a high point of the account"),
    ("max_drawdown_pct", "Max drawdown %", "%", "The same fall as a percent of that high"),
    ("win_rate_pct", "Win rate", "%", "Trades that made money"),
    ("trades", "Trades", "trades", "Number of closed trades"),
    ("profit_factor", "Profit factor", "ratio", "Money won / money lost; above 1 is profitable"),
    ("sharpe", "Sharpe", "ratio", "Return per unit of risk (yearly). Above 1 is good"),
    ("sortino", "Sortino", "ratio", "Like Sharpe, but only losing days count as risk"),
    ("calmar", "Calmar", "ratio", "CAGR divided by the max drawdown"),
    ("expectancy", "Expectancy", "Rs", "Average P&L per trade after costs"),
    ("avg_win", "Average win", "Rs", "Average P&L of the winning trades"),
    ("avg_loss", "Average loss", "Rs", "Average P&L of the losing trades"),
    ("max_win_streak", "Max winning streak", "trades", "Most winning trades in a row"),
    ("max_loss_streak", "Max losing streak", "trades", "Most losing trades in a row"),
    ("trading_days", "Days with trades", "days", "Days on which at least one trade closed"),
    ("best_day", "Best day", "Rs", "The day with the biggest profit"),
    ("worst_day", "Worst day", "Rs", "The day with the biggest loss"),
    ("total_costs", "Total costs", "Rs", "Brokerage, taxes and slippage (already taken out of the P&L)"),
]


# The cards: one {key, label, value, unit, help} per metric, value None when it cannot be worked out.
def metric_list(result: BacktestResult) -> list[dict]:
    values = dict(result.metrics, total_costs=round(sum(t.costs for t in result.trades), 2))
    return [{"key": key, "label": label, "value": to_json_value(values.get(key)), "unit": unit, "help": help_text}
            for key, label, unit, help_text in METRICS]


# A pandas Series indexed by date as [{"time": "YYYY-MM-DD", "value": number}, ...].
def series_points(series: pd.Series | None) -> list[dict]:
    if series is None:
        return []
    return [{"time": to_json_value(pd.Timestamp(i).date()), "value": to_json_value(v)} for i, v in series.items()]


# Account value per day, starting with the capital on the first day (like the equity chart).
def equity_points(result: BacktestResult) -> list[dict]:
    equity = result.equity
    if result.start not in equity.index:
        equity = pd.concat([pd.Series([result.capital], index=[result.start]), equity])
    return series_points(equity)


# Percent (and rupees) below the highest account value so far, per day.
def drawdown_points(result: BacktestResult) -> list[dict]:
    dd = drawdown(result.equity, result.capital)
    return [{"time": to_json_value(pd.Timestamp(i).date()), "value": to_json_value(row.dd_pct),
             "rs": to_json_value(row.dd_rs)} for i, row in dd.iterrows()]


# P&L per period and its return on the account value at the start of that period.
def period_returns(result: BacktestResult, monthly: bool) -> list[dict]:
    daily = result.daily_pnl
    if daily.empty:
        return []
    dates = pd.to_datetime(pd.Series(daily.index))
    keys = ["year", "month"] if monthly else ["year"]
    frame = pd.DataFrame({"year": dates.dt.year.values, "month": dates.dt.month.values, "pnl": daily.values})
    grouped = frame.groupby(keys, sort=True)["pnl"].sum()
    rows, value = [], result.capital                   # the account value at the start of each period
    for key, pnl in grouped.items():
        key = key if isinstance(key, tuple) else (key,)
        row = dict(zip(keys, (int(k) for k in key)))
        row.update(pnl=round(float(pnl), 2), return_pct=round(float(pnl) / value * 100, 2) if value else None)
        rows.append(row)
        value += float(pnl)
    return rows


# The days a backtester skipped and why (empty when it skipped none).
def skipped_rows(result: BacktestResult) -> list[dict]:
    skipped = result.extras.get("skipped")
    if skipped is None:
        return []
    return df_to_records(pd.DataFrame(skipped))


# Everything a results screen shows, as plain JSON-ready values.
def result_to_json(result: BacktestResult) -> dict:
    trades = result.trades_frame()
    has_trades = bool(result.trades)
    return {
        "name": result.name, "start": result.start.isoformat(), "end": result.end.isoformat(),
        "capital": to_json_value(result.capital), "is_demo": result.is_demo,
        "params": {str(k): to_json_value(v) if not isinstance(v, (list, dict)) else v
                   for k, v in result.params.items()},
        "metrics": metric_list(result),
        "equity": equity_points(result),
        "drawdown": drawdown_points(result),
        "benchmark_name": result.benchmark_name or None,
        "benchmark": series_points(result.benchmark) if result.benchmark is not None else None,
        "yearly_returns": period_returns(result, monthly=False),
        "monthly_returns": period_returns(result, monthly=True),
        "day_heatmap": fig_to_json(charts.daily_heatmap(result)) if has_trades else None,
        "month_heatmap": fig_to_json(charts.monthly_heatmap(result)) if has_trades else None,
        "trades": df_to_records(trades.tail(MAX_TRADES)), "trades_total": len(trades),
        "skipped_days": skipped_rows(result),
        "run_id": to_json_value(result.extras.get("run_id")),
        "historical": True, "note": NOTE,
    }
