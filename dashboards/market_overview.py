"""Module 1 - Live Market Overview.

Top: index cards (Nifty 50, Bank Nifty, Fin Nifty, Sensex, India VIX), Nifty 50 top
gainers/losers and market breadth. Refreshes every 60 seconds while the market is
open (09:15-15:30 IST, weekdays). Uses Dhan quotes when the keys are set, otherwise
yfinance (about 15 minutes delayed). Logic lives in marketlens/overview.py.
Middle: sector heatmap (Nifty sector indices, % change over 1 day / 1 week / 1 month).
Bottom: price chart with moving averages, crossover signals, optional 200-day MA and
Bollinger Bands, plus RSI(14) and volume panels (requirements FR-06, FR-08).
Indicator maths lives in marketlens/signals/indicators.py (shared with module 7).
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from marketlens.data import add_moving_averages, get_history
from marketlens.overview import (SOURCE_YF, WINDOWS, breadth, change_colour, get_sector_heatmap,
                                 get_snapshot, market_is_open, market_status, top_movers)
from marketlens.signals.indicators import bollinger, rsi, sma
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
    biggest = max(table["pct_change"].abs().max(), 0.5)   # keep the colours readable on quiet days
    fig = go.Figure(go.Treemap(
        labels=table["sector"], parents=[""] * len(table), values=[1] * len(table),
        customdata=table[["pct_change", "last"]].to_numpy(),
        texttemplate="<b>%{label}</b><br>%{customdata[0]:+.2f}%",
        hovertemplate="%{label}<br>Change: %{customdata[0]:+.2f}%<br>Index: %{customdata[1]:,.2f}<extra></extra>",
        marker=dict(colors=table["pct_change"], colorscale="RdYlGn", cmid=0, cmin=-biggest,
                    cmax=biggest, colorbar=dict(title="%")),
    ))
    fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")
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
EXTRA = {"6mo": ("2y", 6), "1y": ("2y", 12), "2y": ("5y", 24), "5y": ("10y", 60)}
fetch_period, months = EXTRA[period]
full = get_history(ticker, period=fetch_period)
if full.empty:
    st.error(f"No data found for {ticker}. Check the spelling and the .NS ending.")
    st.stop()

full = add_moving_averages(full, fast, slow)
full["MA200"] = sma(full["Close"], 200)
full["RSI"] = rsi(full["Close"], 14)
full = full.join(bollinger(full["Close"], 20, 2))
df = full[full.index >= full.index[-1] - pd.DateOffset(months=months)]
buys = df[df["Signal"] == 1]
sells = df[df["Signal"] == -1]

fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                    row_heights=[0.6, 0.2, 0.2], subplot_titles=("", "RSI (14)", "Volume"))
fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"],
                             low=df["Low"], close=df["Close"], name="Price"), row=1, col=1)
fig.add_trace(go.Scatter(x=df.index, y=df[f"MA{fast}"], name=f"{fast}-day MA"), row=1, col=1)
fig.add_trace(go.Scatter(x=df.index, y=df[f"MA{slow}"], name=f"{slow}-day MA"), row=1, col=1)
if show_ma200:
    fig.add_trace(go.Scatter(x=df.index, y=df["MA200"], name="200-day MA",
                             line=dict(color="purple", width=2)), row=1, col=1)
if show_bb:
    fig.add_trace(go.Scatter(x=df.index, y=df["upper"], name="Bollinger upper",
                             line=dict(color="gray", width=1, dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["lower"], name="Bollinger lower", fill="tonexty",
                             fillcolor="rgba(128,128,128,0.12)",
                             line=dict(color="gray", width=1, dash="dot")), row=1, col=1)
fig.add_trace(go.Scatter(x=buys.index, y=buys["Close"], mode="markers", name="Bullish cross",
                         marker=dict(symbol="triangle-up", size=12, color="limegreen")), row=1, col=1)
fig.add_trace(go.Scatter(x=sells.index, y=sells["Close"], mode="markers", name="Bearish cross",
                         marker=dict(symbol="triangle-down", size=12, color="red")), row=1, col=1)

# RSI panel with the usual 70 / 30 lines
fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], name="RSI 14", line=dict(color="#2e86de")), row=2, col=1)
fig.add_hrect(y0=30, y1=70, fillcolor="rgba(46,134,222,0.08)", line_width=0, row=2, col=1)
for level in (70, 30):
    fig.add_hline(y=level, line_dash="dash", line_color="gray", line_width=1, row=2, col=1)

# Volume panel: green when the day closed up, red when it closed down
colours = ["#16a34a" if c >= o else "#dc2626" for o, c in zip(df["Open"], df["Close"])]
fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="Volume", marker_color=colours,
                     showlegend=False), row=3, col=1)

fig.update_layout(height=800, xaxis_rangeslider_visible=False, legend=dict(orientation="h"))
fig.update_yaxes(title_text="Price (Rs)", row=1, col=1)
fig.update_yaxes(range=[0, 100], row=2, col=1)
st.plotly_chart(fig, width="stretch")
if df["Volume"].fillna(0).eq(0).all():
    st.caption("This ticker has no volume data on yfinance (indices usually don't).")

# ---- 4. Table of past crossover signals ----
signals = df[df["Signal"] != 0][["Close", f"MA{fast}", f"MA{slow}", "Signal"]].copy()
signals["Signal"] = signals["Signal"].map({1: "Bullish cross", -1: "Bearish cross"})
st.subheader(f"Past crossover signals ({len(signals)})")
st.dataframe(signals.round(2).sort_index(ascending=False), width="stretch")
historical_note()
