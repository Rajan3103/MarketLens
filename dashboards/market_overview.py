"""Market Overview: index cards plus a price chart with 20/50-day moving averages.

Requirements FR-06 and FR-08.
"""
import plotly.graph_objects as go
import streamlit as st

from marketlens.data import INDICES, add_moving_averages, get_history
from marketlens.ui import historical_note

st.title("📊 Market Overview")

# ---- 1. Index cards: last close and change from the day before ----
cols = st.columns(len(INDICES))
for col, (name, ticker) in zip(cols, INDICES.items()):
    df = get_history(ticker, period="5d")
    if len(df) >= 2:
        last, prev = df["Close"].iloc[-1], df["Close"].iloc[-2]
        change = (last - prev) / prev * 100
        col.metric(name, f"{last:,.2f}", f"{change:+.2f}%")
    else:
        col.metric(name, "no data")

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
