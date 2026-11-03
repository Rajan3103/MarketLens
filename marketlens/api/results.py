"""A portfolio backtest result as JSON for the web page's <BacktestResults> component.

    result_json(result)  -> {"summary": {...}, "comparison": [...], "yearly": [...], "equity_chart": {...},
                             "drawdown_chart": {...}, "holdings": [...], "rebalances": [...], "trades": [...], ...}

Works for any BacktestResult whose extras have "benchmarks" (name -> value curve), "rebalances" and
"holdings" (the momentum engine, module 8). Every chart is Plotly JSON, every table a list of rows.
"""
import plotly.graph_objects as go

from marketlens.api.schemas import df_to_records, fig_to_json, to_json_value
from marketlens.backtest.charts import drawdown_chart, equity_chart
from marketlens.backtest.engine import BacktestResult
from marketlens.momentum.portfolio_bt import comparison_table, yearly_table

NOTE = "Historical analysis for learning, not a live trading recommendation."
EXTRA_COLORS = ["#7c3aed", "#0d9488", "#ca8a04"]
TRADES_SHOWN = 2000                  # newest trades sent to the page; the CSV / Excel report has all of them


# The value chart: strategy, Nifty 50 and any other benchmark (e.g. a Nifty 200 Momentum 30 ETF).
def value_chart(result: BacktestResult) -> go.Figure:
    fig = equity_chart(result)
    extra = [(n, s) for n, s in result.extras.get("benchmarks", {}).items() if n != result.benchmark_name]
    for (name, series), color in zip(extra, EXTRA_COLORS):
        fig.add_trace(go.Scatter(x=list(series.index), y=series.values, name=name, line=dict(color=color, width=2)))
    fig.update_layout(title="Portfolio value vs Nifty 50 (same starting capital)")
    return fig


# The numbers for the cards at the top.
def summary(result: BacktestResult) -> dict:
    m, x = result.metrics, result.extras
    keys = ("final_value", "total_return_pct", "cagr_pct", "max_drawdown_pct", "max_drawdown_rs", "volatility_pct",
            "sharpe", "sortino", "calmar", "total_pnl")
    out = {k: to_json_value(m.get(k)) for k in keys}
    out.update({k: to_json_value(x.get(k)) for k in ("charges", "slippage", "tax_paid", "orders", "turnover",
                                                       "days_in_cash")})
    out["rebalance_count"] = len(x.get("rebalances", []))
    out["liquidation"] = {k: to_json_value(v) for k, v in (x.get("liquidation") or {}).items()}
    if result.benchmark is not None:
        out["benchmark_final"] = to_json_value(result.benchmark.iloc[-1])
        out["benchmark_return_pct"] = to_json_value((result.benchmark.iloc[-1] / result.capital - 1) * 100)
    return out


# Everything the results component shows.
def result_json(result: BacktestResult) -> dict:
    x = result.extras
    trades = result.trades_frame().tail(TRADES_SHOWN).iloc[::-1]
    return {"name": result.name, "start": result.start.isoformat(), "end": result.end.isoformat(),
            "capital": result.capital, "is_demo": result.is_demo, "params": result.params,
            "benchmark_name": result.benchmark_name, "summary": summary(result),
            "comparison": df_to_records(comparison_table(result)), "yearly": df_to_records(yearly_table(result)),
            "equity_chart": fig_to_json(value_chart(result)), "drawdown_chart": fig_to_json(drawdown_chart(result)),
            "holdings": df_to_records(_holdings(x.get("holdings"))),
            "rebalances": df_to_records(x["rebalances"]) if len(x.get("rebalances", [])) else [],
            "tax_by_year": df_to_records(x["tax_by_year"]) if "tax_by_year" in x else [],
            "trades": df_to_records(trades), "trades_total": len(result.trades),
            "notes": x.get("notes", [NOTE]), "run_id": x.get("run_id")}


# The final holdings without the rebalance columns.
def _holdings(rows):
    import pandas as pd
    table = pd.DataFrame(rows or [])
    return table.drop(columns=[c for c in ("rebalance_date", "signal_date") if c in table]) if len(table) else table
