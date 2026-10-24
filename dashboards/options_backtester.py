"""Module 4 - Options Backtester (planned). Similar in spirit to AlgoTest."""
import streamlit as st

from marketlens.costs import round_trip_costs
from marketlens.ui import cost_settings_inputs, coming_soon, historical_note, key_notice

st.title("🧪 Options Backtester")
key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
           "Backtests will run on DEMO data until then.")
coming_soon("Options Backtester", [
    "Build a strategy from legs: buy or sell, call or put, strike by ATM offset or by premium",
    "Entry and exit time, stop loss, target and trailing stop for each leg",
    "Run it over months or years of expired Nifty and Bank Nifty options data",
    "Trade list, equity curve, win rate, max drawdown and P&L by weekday and month",
    "Brokerage, taxes and slippage included in the results",
])
# Cost settings this page will use (change them in the box)
costs = cost_settings_inputs("backtester")
example = round_trip_costs("options", 200, 200, 75, costs, entry_side="SELL")
st.caption(f"Example with these settings: selling 1 NIFTY lot (75) at Rs 200 and buying it "
           f"back at Rs 200 costs Rs {example:,.2f}.")
historical_note()
