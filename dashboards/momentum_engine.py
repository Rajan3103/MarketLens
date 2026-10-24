"""Module 8 - Momentum Engine Backtest vs Nifty (planned). Requirements FR-13 to FR-15."""
import streamlit as st

from marketlens.ui import coming_soon, historical_note

st.title("🚀 Momentum Engine Backtest vs Nifty")
coming_soon("Momentum Engine", [
    "Rank Nifty 200 stocks by 3, 6 and 12 month returns",
    "Top-N portfolio rebalanced monthly or weekly",
    "Equity curve against Nifty 50 buy-and-hold",
    "CAGR, max drawdown and Sharpe ratio after costs",
])
historical_note()
