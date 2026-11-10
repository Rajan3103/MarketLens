"""Module 1 - Live Market Overview.

Top: index cards (Nifty 50, Bank Nifty, Fin Nifty, Sensex, India VIX), Nifty 50 top
gainers/losers and market breadth. Refreshes every 60 seconds while the market is
open (09:15-15:30 IST, weekdays). Uses Dhan quotes when the keys are set, otherwise
yfinance (about 15 minutes delayed). Logic lives in marketlens/overview.py.
Middle: sector heatmap (Nifty sector indices, % change over 1 day / 1 week / 1 month).
Bottom: price chart with moving averages, crossover signals, optional 200-day MA and
Bollinger Bands, plus RSI(14) and volume panels (requirements FR-06, FR-08).
Indicator maths lives in marketlens/signals/indicators.py (shared with module 7); the tables and charts
of this page are built in marketlens/overview.py, shared with the web page (frontend/app/market-overview).
"""
import streamlit as st

from marketlens.data import get_history
from marketlens.overview import (CHART_PERIODS, SOURCE_YF, WINDOWS, breadth, breadth_chart, change_colour,
                                 get_sector_heatmap, get_snapshot, has_no_volume, indicator_chart, indicator_table,
                                 market_is_open, market_status, sector_chart, top_movers)
from marketlens.ui import help_note, historical_note

st.title("📊 Live Market Overview")
help_note("What this page does: index cards, Nifty 50 top movers and breadth, a sector heatmap and a daily "
          "price chart with moving averages, RSI and volume (refreshes every 60 seconds in market hours).\n"
          "How to read it: prices in Rs or index points, changes in % from yesterday's close; on the chart a "
          "green ▲ is a bullish crossover (fast average above slow), a red ▼ a bearish one; averages are in "
          "trading days.")


# Fetch the overview at most once every 55 seconds (the section refreshes every 60).
@st.cache_data(ttl=55, show_spinner="Fetching prices ...")
def cached_snapshot():
    return get_snapshot()


# A small table of movers with green / red change columns.
def movers_table(df):
    view = df[["symbol", "last", "change", "pct_change"]].rename(
        columns={"symbol": "Stock", "last": "Price", "change": "Change", "pct_change": "% Change"})
    return view.style.map(change_colour, subset=["Change", "% Change"]).format(
        {"Price": "{:,.2f}", "Change": "{:+,.2f}", "% Change": "{:+.2f}%"})


OPEN_NOW = market_is_open()


# Everything in here re-runs by itself every 60 s while the market is open.
@st.fragment(run_every=60 if OPEN_NOW else None)
def live_section():
    if market_is_open():
        st.success(f"🟢 {market_status()}")
    else:
        st.info(f"⚪ {market_status()}")
    try:
        snap = cached_snapshot()
    except Exception as err:
        st.error(f"Could not fetch prices: {err}")
        return

    # ---- 1. Index cards ----
    cols = st.columns(len(snap.indices))
    for col, row in zip(cols, snap.indices.itertuples()):
        if row.last != row.last or row.prev_close != row.prev_close:   # NaN = no data
            col.metric(row.name, "no data")
            continue
        col.metric(row.name, f"{row.last:,.2f}", f"{row.change:+,.2f} ({row.pct_change:+.2f}%)",
                   # VIX going up is bad news, so show its rise in red
                   delta_color="inverse" if row.symbol == "INDIAVIX" else "normal")
    note = " Add the Dhan keys in .env for live prices." if snap.source == SOURCE_YF else ""
    st.caption(f"Source: {snap.source} · fetched {snap.as_of:%d-%b-%Y %H:%M:%S} IST.{note}")

    # ---- 2. Top gainers / losers and market breadth (Nifty 50) ----
    gainers, losers = top_movers(snap.stocks, 5)
    left, right = st.columns(2)
    left.markdown("**🟢 Top 5 gainers (Nifty 50)**")
    left.dataframe(movers_table(gainers), hide_index=True, width="stretch")
    right.markdown("**🔴 Top 5 losers (Nifty 50)**")
    right.dataframe(movers_table(losers), hide_index=True, width="stretch")

    b = breadth(snap.stocks)
    st.markdown(f"**Market breadth (Nifty 50, {len(snap.stocks)} stocks)**")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Advances", b["advances"])
    m2.metric("Declines", b["declines"])
    m3.metric("Unchanged", b["unchanged"])
    m4.metric("Advance / decline ratio", b["ad_ratio"] if b["ad_ratio"] is not None else "all up")
    st.plotly_chart(breadth_chart(b), width="stretch")


live_section()
st.divider()


# Sector heatmap data, fetched at most every 5 minutes.
@st.cache_data(ttl=300, show_spinner="Fetching sector indices ...")
def cached_sectors(window: str):
    return get_sector_heatmap(window)


# The sector heatmap re-runs by itself every 60 s while the market is open.
@st.fragment(run_every=60 if OPEN_NOW else None)
def sector_section():
    st.subheader("Sector heatmap")
    window = st.radio("Change over", list(WINDOWS), horizontal=True, key="sector_window")
    try:
        table, skipped = cached_sectors(window)
    except Exception as err:
        st.error(f"Could not fetch sector indices: {err}")
        return
    if table.empty:
        st.info("No sector data available right now.")
        return
    st.plotly_chart(sector_chart(table), width="stretch")
    note = f" Skipped (not enough data on yfinance): {', '.join(skipped)}." if skipped else ""
    st.caption(f"Nifty sector indices from yfinance, {window} change.{note}")


sector_section()
st.divider()

# ---- 3. Price chart for any ticker ----
st.subheader("Price chart with indicators")
left, mid, right = st.columns([2, 1, 1])
# NSE stocks on Yahoo Finance end with .NS (e.g. TCS.NS, INFY.NS)
ticker = left.text_input("Ticker", value="RELIANCE.NS").strip().upper()
period = mid.selectbox("Timeframe", ["6mo", "1y", "2y", "5y"], index=1)
fast, slow = right.slider("Fast / slow average (days)", 5, 200, (20, 50))
t1, t2 = st.columns(2)
show_ma200 = t1.checkbox("200-day MA", value=False)
show_bb = t2.checkbox("Bollinger Bands (20, 2)", value=False)

# Fetch extra history before the chosen period so long averages and RSI are ready on day one
fetch_period, months = CHART_PERIODS[period]
full = get_history(ticker, period=fetch_period)
if full.empty:
    st.error(f"No data found for {ticker}. Check the spelling and the .NS ending.")
    st.stop()
if fast >= slow:
    st.error("The fast average must be shorter than the slow one.")
    st.stop()

df = indicator_table(full, fast, slow, months)
st.plotly_chart(indicator_chart(df, fast, slow, show_ma200, show_bb), width="stretch")
if has_no_volume(df):
    st.caption("This ticker has no volume data on yfinance (indices usually don't).")

# ---- 4. Table of past crossover signals ----
signals = df[df["Signal"] != 0][["Close", f"MA{fast}", f"MA{slow}", "Signal"]].copy()
signals["Signal"] = signals["Signal"].map({1: "Bullish cross", -1: "Bearish cross"})
st.subheader(f"Past crossover signals ({len(signals)})")
st.dataframe(signals.round(2).sort_index(ascending=False), width="stretch")
historical_note()
