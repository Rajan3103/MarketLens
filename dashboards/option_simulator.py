"""Module 5 - Option Simulator (planned). Similar in spirit to StockMock."""
import streamlit as st

from marketlens.ui import coming_soon, historical_note, key_notice

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
historical_note()
