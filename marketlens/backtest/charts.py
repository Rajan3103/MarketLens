"""Plotly charts for any BacktestResult.

  equity_chart      account value vs the benchmark (same Rs scale, one axis)
  drawdown_chart    how far below its high the account was, in percent
  daily_heatmap     one square per trading day (weeks across, weekdays down), like a calendar
  monthly_heatmap   years down, months across, with each month's P&L written in
  weekday_chart     total P&L by day of the week

Profit is blue and loss is red, with grey for "about zero" (easy to read for colour-blind users too).
"""
import calendar

import pandas as pd
import plotly.graph_objects as go

from marketlens.backtest.engine import BacktestResult
from marketlens.backtest.metrics import drawdown

STRATEGY_COLOR = "#2a78d6"
BENCHMARK_COLOR = "#eb6834"
PROFIT_COLOR = "#2a78d6"
LOSS_COLOR = "#e34948"
ZERO_COLOR = "#f0efec"
PNL_SCALE = [[0.0, LOSS_COLOR], [0.5, ZERO_COLOR], [1.0, PROFIT_COLOR]]   # red - grey - blue
MONTHS = list(calendar.month_abbr)[1:]           # Jan .. Dec
WEEKDAYS = list(calendar.day_abbr)               # Mon .. Sun


# Shared look: hover on the x position, a horizontal legend and small margins.
def _layout(fig: go.Figure, title: str, y_title: str, height: int = 380) -> go.Figure:
    fig.update_layout(title=title, height=height, hovermode="x unified", yaxis_title=y_title,
                      legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom"),
                      margin=dict(l=10, r=10, t=60, b=10))
    return fig


# Short rupee text: 1,25,000 -> "1.25L", 4,500 -> "4.5k".
def short_rs(value: float) -> str:
    if pd.isna(value):
        return ""
    for size, unit in ((1e7, "Cr"), (1e5, "L"), (1e3, "k")):
        if abs(value) >= size:
            return f"{value / size:.2f}".rstrip("0").rstrip(".") + unit
    return f"{value:.0f}"


# Equity curve vs the benchmark (both start at the same capital).
def equity_chart(result: BacktestResult) -> go.Figure:
    fig = go.Figure()
    start = pd.Series([result.capital], index=[result.start])
    equity = pd.concat([start, result.equity]) if result.start not in result.equity.index else result.equity
    fig.add_trace(go.Scatter(x=equity.index, y=equity.values, name="Strategy",
                             line=dict(color=STRATEGY_COLOR, width=2)))
    if result.benchmark is not None:
        fig.add_trace(go.Scatter(x=result.benchmark.index, y=result.benchmark.values,
                                 name=f"{result.benchmark_name or 'Benchmark'} (same capital)",
                                 line=dict(color=BENCHMARK_COLOR, width=2)))
    fig.add_hline(y=result.capital, line_color="gray", line_dash="dot", annotation_text="Capital")
    fig.update_yaxes(tickprefix="Rs ", tickformat=",.0f")
    return _layout(fig, "Equity curve", "Account value (Rs)")


# Drawdown: percent below the highest account value so far (0 = at a new high).
def drawdown_chart(result: BacktestResult) -> go.Figure:
    dd = drawdown(result.equity, result.capital)
    fig = go.Figure(go.Scatter(x=dd.index, y=dd["dd_pct"], fill="tozeroy", name="Drawdown",
                               line=dict(color=LOSS_COLOR, width=2), fillcolor="rgba(227,73,72,0.25)",
                               customdata=dd["dd_rs"],
                               hovertemplate="%{y:.2f}% (Rs %{customdata:,.0f})<extra></extra>"))
    fig.update_yaxes(ticksuffix="%")
    return _layout(fig, "Drawdown", "Below the high (%)", height=280)


# The day-wise table behind the calendar: week (Monday's date), weekday and P&L.
def calendar_table(result: BacktestResult) -> pd.DataFrame:
    daily = result.daily_pnl
    if daily.empty:
        return pd.DataFrame(columns=["date", "week", "weekday", "pnl"])
    dates = pd.to_datetime(pd.Series(daily.index))
    return pd.DataFrame({"date": dates.dt.date, "week": (dates - pd.to_timedelta(dates.dt.weekday, "D")).dt.date,
                         "weekday": dates.dt.weekday, "pnl": daily.values})


# Calendar heatmap: one column per week, one row per weekday, colour = that day's P&L.
def daily_heatmap(result: BacktestResult) -> go.Figure:
    table = calendar_table(result)
    if table.empty:
        return _layout(go.Figure(), "Day-wise P&L", "")
    days = sorted(set(range(5)) | set(table["weekday"]))          # Mon-Fri, plus any weekend sessions
    grid = table.pivot(index="weekday", columns="week", values="pnl").reindex(days)
    dates = table.pivot(index="weekday", columns="week", values="date").reindex(days)
    labels = dates.map(lambda d: f"{d:%a %d-%b-%Y}" if pd.notna(d) else "no trade")
    fig = go.Figure(go.Heatmap(
        z=grid.values, x=list(grid.columns), y=[WEEKDAYS[d] for d in days], customdata=labels.values,
        colorscale=PNL_SCALE, zmid=0, xgap=2, ygap=2, colorbar=dict(title="Rs"),
        hovertemplate="%{customdata}<br>P&L Rs %{z:,.0f}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(hovermode="closest")
    return _layout(fig, "Day-wise P&L (each square is one day)", "", height=260)


# Month-wise P&L as a table: one row per year, Jan..Dec columns and a Total column.
def monthly_table(result: BacktestResult) -> pd.DataFrame:
    daily = result.daily_pnl
    if daily.empty:
        return pd.DataFrame(columns=["Year", *MONTHS, "Total"])
    dates = pd.to_datetime(pd.Series(daily.index))
    frame = pd.DataFrame({"Year": dates.dt.year, "Month": dates.dt.month, "pnl": daily.values})
    table = frame.pivot_table(index="Year", columns="Month", values="pnl", aggfunc="sum")
    table = table.reindex(columns=range(1, 13))
    table.columns = MONTHS
    table["Total"] = table.sum(axis=1)
    return table.round(2).reset_index()


# Month-wise heatmap: years down, months across, each month's P&L written inside.
def monthly_heatmap(result: BacktestResult) -> go.Figure:
    table = monthly_table(result)
    if table.empty:
        return _layout(go.Figure(), "Month-wise P&L", "")
    grid = table.set_index("Year")[MONTHS]
    fig = go.Figure(go.Heatmap(
        z=grid.values, x=MONTHS, y=[str(y) for y in grid.index], colorscale=PNL_SCALE, zmid=0,
        xgap=2, ygap=2, text=grid.map(short_rs).values, texttemplate="%{text}",
        colorbar=dict(title="Rs"), hovertemplate="%{x} %{y}<br>P&L Rs %{z:,.0f}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", type="category")
    fig.update_layout(hovermode="closest")
    return _layout(fig, "Month-wise P&L", "", height=140 + 50 * len(grid))


# Total P&L, number of days and win rate for each day of the week.
def weekday_table(result: BacktestResult) -> pd.DataFrame:
    table = calendar_table(result)
    if table.empty:
        return pd.DataFrame(columns=["Weekday", "Total P&L", "Days", "Winning days %", "Average P&L"])
    grouped = table.groupby("weekday")["pnl"]
    out = pd.DataFrame({"Total P&L": grouped.sum(), "Days": grouped.size(),
                        "Winning days %": grouped.apply(lambda s: (s > 0).mean() * 100),
                        "Average P&L": grouped.mean()}).round(2)
    out.insert(0, "Weekday", [WEEKDAYS[d] for d in out.index])
    return out.reset_index(drop=True)


# Bar chart of total P&L by weekday (blue = profit, red = loss).
def weekday_chart(result: BacktestResult) -> go.Figure:
    table = weekday_table(result)
    fig = go.Figure(go.Bar(
        x=table["Weekday"], y=table["Total P&L"], name="P&L",
        marker_color=[PROFIT_COLOR if v >= 0 else LOSS_COLOR for v in table["Total P&L"]],
        text=[short_rs(v) for v in table["Total P&L"]], textposition="outside",
        customdata=table[["Days", "Winning days %"]].values,
        hovertemplate="%{x}: Rs %{y:,.0f}<br>%{customdata[0]} days, %{customdata[1]:.0f}% winning<extra></extra>"))
    fig.add_hline(y=0, line_color="gray")
    fig.update_layout(hovermode="closest")
    return _layout(fig, "P&L by weekday", "Total P&L (Rs)", height=320)
