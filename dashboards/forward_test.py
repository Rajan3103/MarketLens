"""Module 6 - Forward Test / Paper Trading (planned)."""
import streamlit as st

from marketlens.ui import coming_soon, historical_note, trading_mode_banner

st.title("📝 Forward Test / Paper Trading")
trading_mode_banner()
coming_soon("Forward Test / Paper Trading", [
    "Run a saved strategy on today's live market with virtual money",
    "Virtual order book, positions and P&L that update during the day",
    "Compare forward-test results with the strategy's backtest",
    "Daily P&L log saved to the database",
])
historical_note()
