"""Module 5 - Option Simulator (planned). Similar in spirit to StockMock."""
import streamlit as st

from marketlens.costs import round_trip_costs
from marketlens.ui import cost_settings_inputs, coming_soon, historical_note, key_notice

st.title("⏪ Option Simulator")
key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
           "The simulator will replay a DEMO day until then.")
coming_soon("Option Simulator", [
    "Pick any past trading day and replay it minute by minute",
    "Buy and sell options at the prices of that moment with virtual money",
    "Pause, play faster or jump to a time",
    "Live P&L, payoff chart and Greeks as the day moves",
    "Save each practice session to your analytics",
])
# Cost settings this page will use (change them in the box)
costs = cost_settings_inputs("simulator")
example = round_trip_costs("options", 200, 200, 65, costs, entry_side="SELL")
st.caption(f"Example with these settings: selling 1 NIFTY lot (65) at Rs 200 and buying it "
           f"back at Rs 200 costs Rs {example:,.2f}.")
historical_note()
