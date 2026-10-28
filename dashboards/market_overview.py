"""Module 1 - Live Market Overview.

Top: index cards (Nifty 50, Bank Nifty, Fin Nifty, Sensex, India VIX), Nifty 50 top
gainers/losers and market breadth. Refreshes every 60 seconds while the market is
open (09:15-15:30 IST, weekdays). Uses Dhan quotes when the keys are set, otherwise
yfinance (about 15 minutes delayed). Logic lives in marketlens/overview.py.
Bottom: price chart with moving averages and crossover signals (requirements FR-06, FR-08).
"""
import plotly.graph_objects as go
import streamlit as st

from marketlens.data import add_moving_averages, get_history
from marketlens.overview import (SOURCE_YF, breadth, change_colour, get_snapshot, market_is_open,
                                 market_status, top_movers)
from marketlens.ui import historical_note

st.title("📊 Live Market Overview")


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
    bar = go.Figure()
    for label, value, colour in (("Advances", b["advances"], "#16a34a"),
                                 ("Unchanged", b["unchanged"], "#9ca3af"),
                                 ("Declines", b["declines"], "#dc2626")):
        bar.add_trace(go.Bar(x=[value], y=["Nifty 50"], orientation="h", name=label,
                             marker_color=colour, text=[value], textposition="inside"))
    bar.update_layout(barmode="stack", height=120, margin=dict(l=0, r=0, t=0, b=0),
                      showlegend=False, xaxis=dict(visible=False))
    st.plotly_chart(bar, width="stretch")


live_section()
st.divider()

# ---- 2. Price chart for any ticker ----
st.subheader("Price chart with moving averages")
left, mid, right = st.columns([2, 1, 1])
# NSE stocks on Yahoo Finance end with .NS (e.g. TCS.NS, INFY.NS)
ticker = left.text_input("Ticker", value="RELIANCE.NS").strip().upper()
period = mid.selectbox("Timeframe", ["6mo", "1y", "2y", "5y"], index=1)
fast, slow = right.slider("Fast / slow average (days)", 5, 200, (20, 50))

df = get_history(ticker, period=period)
if df.empty:
    st.error(f"No data found for {ticker}. Check the spelling and the .NS ending.")
    st.stop()

df = add_moving_averages(df, fast, slow)
buys = df[df["Signal"] == 1]
sells = df[df["Signal"] == -1]

fig = go.Figure()
fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"],
                             low=df["Low"], close=df["Close"], name="Price"))
fig.add_trace(go.Scatter(x=df.index, y=df[f"MA{fast}"], name=f"{fast}-day MA"))
fig.add_trace(go.Scatter(x=df.index, y=df[f"MA{slow}"], name=f"{slow}-day MA"))
fig.add_trace(go.Scatter(x=buys.index, y=buys["Close"], mode="markers", name="Bullish cross",
                         marker=dict(symbol="triangle-up", size=12, color="limegreen")))
fig.add_trace(go.Scatter(x=sells.index, y=sells["Close"], mode="markers", name="Bearish cross",
                         marker=dict(symbol="triangle-down", size=12, color="red")))
fig.update_layout(height=550, xaxis_rangeslider_visible=False,
                  yaxis_title="Price (Rs)", legend=dict(orientation="h"))
st.plotly_chart(fig, width="stretch")

# ---- 3. Table of past crossover signals ----
signals = df[df["Signal"] != 0][["Close", f"MA{fast}", f"MA{slow}", "Signal"]].copy()
signals["Signal"] = signals["Signal"].map({1: "Bullish cross", -1: "Bearish cross"})
st.subheader(f"Past crossover signals ({len(signals)})")
st.dataframe(signals.round(2).sort_index(ascending=False), width="stretch")
historical_note()
