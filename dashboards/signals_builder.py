"""Module 7 - Signals & Indicator Strategy Builder.

Tab 1 (planned): build buy/sell rules from indicators, backtest them, and
receive TradingView / Chartink webhook alerts.
Tab 2 (ready): Pine Script export for a moving-average crossover.
"""
import streamlit as st

from marketlens.ui import coming_soon, historical_note

st.title("📐 Signals & Indicator Strategy Builder")
builder_tab, pine_tab = st.tabs(["Strategy builder", "Pine Script export"])

# ---- Tab 1: indicator strategy builder (planned) ----
with builder_tab:
    coming_soon("Signals & Indicator Strategy Builder", [
        "Build entry and exit rules from indicators: moving averages, RSI, MACD, Supertrend, VWAP",
        "Backtest the rules on any stock, ETF or index and see every signal on the chart",
        "Receive TradingView and Chartink alerts through a webhook and turn them into paper orders",
        "Export the same rules as TradingView Pine Script",
    ])

# ---- Tab 2: Pine Script export (ready: MA crossover) ----
with pine_tab:
    st.write("Fill in the settings, then copy the code into TradingView's Pine Editor "
             "and click *Add to chart*.")

    c1, c2 = st.columns(2)
    fast = c1.number_input("Fast moving average (days)", value=20, min_value=2)
    slow = c2.number_input("Slow moving average (days)", value=50, min_value=3)

    # The finished Pine Script, with the user's numbers filled in
    code = f"""//@version=6
strategy("MarketLens MA Crossover {fast}/{slow}", overlay=true)

fastMA = ta.sma(close, {fast})
slowMA = ta.sma(close, {slow})
plot(fastMA, "Fast MA", color.green)
plot(slowMA, "Slow MA", color.red)

// Buy when the fast average crosses above the slow one
if ta.crossover(fastMA, slowMA)
    strategy.entry("Long", strategy.long)

// Exit when it crosses back below
if ta.crossunder(fastMA, slowMA)
    strategy.close("Long")
"""

    st.code(code, language="javascript")
    st.download_button("Download .pine file", code, file_name=f"ma_crossover_{fast}_{slow}.pine")

historical_note()
