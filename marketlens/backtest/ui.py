"""Draw a whole backtest result on a Streamlit page with one call:

    from marketlens.backtest.ui import show_backtest
    show_backtest(result)

Used by every backtest page (modules 4, 7, 8 and 9), so they all look the same.
"""
import streamlit as st

from marketlens.backtest import charts
from marketlens.backtest.engine import BacktestResult
from marketlens.backtest.report import export_excel, export_trades_csv, file_stem
from marketlens.ui import historical_note


# Rupees with commas, or "-" when missing.
def _rs(value) -> str:
    return "-" if value is None else f"Rs {value:,.0f}"


# A plain number with 2 decimals (and an optional sign), or "-".
def _num(value, suffix: str = "") -> str:
    return "-" if value is None else f"{value:,.2f}{suffix}"


# The metric cards: three rows of five.
def metric_cards(result: BacktestResult):
    m = result.metrics
    row = st.columns(5)
    row[0].metric("Total P&L", _rs(m["total_pnl"]), _num(m["return_pct"], "% of capital"))
    row[1].metric("CAGR", _num(m["cagr_pct"], "%"), help="Yearly growth rate if returns compound")
    row[2].metric("Max drawdown", _rs(m["max_drawdown_rs"]), _num(m["max_drawdown_pct"], "%"),
                  delta_color="off", help="The biggest fall from a high point of the account")
    row[3].metric("Win rate", _num(m["win_rate_pct"], "%"), f"{m['trades']} trades", delta_color="off")
    row[4].metric("Profit factor", _num(m["profit_factor"]), help="Money won / money lost; above 1 is profitable")
    row = st.columns(5)
    row[0].metric("Sharpe", _num(m["sharpe"]), help="Return per unit of risk (yearly). Above 1 is good")
    row[1].metric("Sortino", _num(m["sortino"]), help="Like Sharpe, but only losing days count as risk")
    row[2].metric("Calmar", _num(m["calmar"]), help="CAGR divided by the max drawdown")
    row[3].metric("Expectancy", _rs(m["expectancy"]), help="Average P&L per trade after costs")
    row[4].metric("Avg win / avg loss", f"{_rs(m['avg_win'])} / {_rs(m['avg_loss'])}")
    row = st.columns(5)
    row[0].metric("Max winning streak", m["max_win_streak"])
    row[1].metric("Max losing streak", m["max_loss_streak"])
    row[2].metric("Best day", _rs(m["best_day"]))
    row[3].metric("Worst day", _rs(m["worst_day"]))
    row[4].metric("Total costs", _rs(sum(t.costs for t in result.trades)),
                  help="Brokerage, taxes and slippage (already taken out of the P&L)")


# Everything about one backtest: cards, charts, trade list, downloads and the learning note.
def show_backtest(result: BacktestResult, key: str = "bt"):
    """key: a short unique name, so two results on one page don't share download buttons."""
    if result.is_demo:
        st.warning("DEMO DATA: these trades are made up to show what the report looks like.")
    st.subheader(result.name)
    st.caption(f"{result.start:%d-%b-%Y} to {result.end:%d-%b-%Y} · capital Rs {result.capital:,.0f} · "
               "all P&L is after brokerage, taxes and slippage")
    if not result.trades:
        st.info("No trades in this period, so there is nothing to show.")
        historical_note()
        return
    metric_cards(result)

    st.plotly_chart(charts.equity_chart(result), width="stretch", key=f"{key}_equity")
    st.plotly_chart(charts.drawdown_chart(result), width="stretch", key=f"{key}_dd")
    day_tab, month_tab, weekday_tab = st.tabs(["📅 Day-wise", "🗓️ Month-wise", "📊 By weekday"])
    with day_tab:
        st.plotly_chart(charts.daily_heatmap(result), width="stretch", key=f"{key}_daily")
    with month_tab:
        st.plotly_chart(charts.monthly_heatmap(result), width="stretch", key=f"{key}_monthly")
        st.dataframe(charts.monthly_table(result), hide_index=True, width="stretch")
    with weekday_tab:
        st.plotly_chart(charts.weekday_chart(result), width="stretch", key=f"{key}_weekday")
        st.dataframe(charts.weekday_table(result), hide_index=True, width="stretch")

    st.markdown(f"**Trade list** ({len(result.trades)} trades)")
    trades = result.trades_frame()
    st.dataframe(trades, hide_index=True, width="stretch", column_config={
        "entry_time": st.column_config.DatetimeColumn("Entry", format="DD-MMM-YYYY HH:mm"),
        "exit_time": st.column_config.DatetimeColumn("Exit", format="DD-MMM-YYYY HH:mm"),
        "gross_pnl": st.column_config.NumberColumn("Gross P&L", format="%.2f"),
        "costs": st.column_config.NumberColumn("Costs", format="%.2f"),
        "pnl": st.column_config.NumberColumn("Net P&L", format="%.2f")})

    stem = ("DEMO_" if result.is_demo else "") + file_stem(result)
    d1, d2 = st.columns(2)
    try:
        d1.download_button("⬇️ Excel report (Summary, Trades, Daily, Monthly)", export_excel(result),
                           f"{stem}.xlsx", key=f"{key}_xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except ValueError as err:          # more rows than Excel can hold
        d1.warning(str(err))
    d2.download_button("⬇️ Trades CSV", export_trades_csv(result), f"{stem}_trades.csv",
                       mime="text/csv", key=f"{key}_csv")
    historical_note()
