"""ETF Momentum Portfolio (planned). Requirements FR-16 and FR-17."""
import streamlit as st

from marketlens.ui import coming_soon

st.title("🧺 ETF Momentum Portfolio")
coming_soon("ETF Momentum Portfolio", [
    "Rotate among NSE ETFs: Nifty, Bank, IT, Gold, Silver, US index",
    "Hold the top K ETFs by momentum, rebalanced monthly",
    "Backtested equity curve, CAGR and drawdown against Nifty 50",
])
