"""Module 4 - Options Backtester (planned). Similar in spirit to AlgoTest."""
import streamlit as st

from marketlens.ui import coming_soon, historical_note, key_notice

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
historical_note()
