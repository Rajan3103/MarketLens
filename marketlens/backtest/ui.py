"""Draw a whole backtest result on a Streamlit page with one call:

    from marketlens.backtest.ui import show_backtest
    show_backtest(result)

Used by every backtest page (modules 4, 7, 8 and 9), so they all look the same.
"""
import streamlit as st

from marketlens.backtest import charts
from marketlens.backtest.engine import BacktestResult
from marketlens.backtest.present import metric_list
from marketlens.backtest.report import export_excel, export_trades_csv, file_stem
from marketlens.ui import historical_note


# One card's value as text: "Rs 1,234", "12.34%", "1.25", "7", or "-" when missing.
def card_text(value, unit: str) -> str:
    if value is None:
        return "-"
    if unit == "Rs":
        return f"Rs {value:,.0f}"
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "ratio":
        return f"{value:,.2f}"
    return f"{value:,.0f}"


# The metric cards in rows of five. The list (labels, units, help) comes from present.metric_list, the same
# one the web API sends, so both screens show the same numbers.
def metric_cards(result: BacktestResult):
    cards = metric_list(result)
    for first in range(0, len(cards), 5):
        row = st.columns(5)
        for column, card in zip(row, cards[first:first + 5]):
            column.metric(card["label"], card_text(card["value"], card["unit"]), help=card["help"])


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
