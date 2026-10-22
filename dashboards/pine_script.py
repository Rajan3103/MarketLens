"""Pine Script Generator: makes TradingView code for a moving-average crossover.

Requirements FR-20 and FR-21. More strategies will be added later.
"""
import streamlit as st

st.title("🌲 Pine Script Generator")
st.write("Fill in the settings, then copy the code into TradingView's Pine Editor and click *Add to chart*.")

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
