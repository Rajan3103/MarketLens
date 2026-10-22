"""Premium-Based Strategies (planned). Requirements FR-11 and FR-12."""
import streamlit as st

from marketlens.ui import coming_soon

st.title("💰 Premium-Based Strategies")
coming_soon("Premium-Based Strategies", [
    "Pick strikes by premium, for example the call and put nearest Rs 50",
    "Entry time, stop loss and target rules",
    "Backtest on expired options data from Dhan",
    "Trade list, equity curve and win rate",
])
