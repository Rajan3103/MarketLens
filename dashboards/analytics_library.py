"""Module 10 - Personal Analytics & Strategy Library (planned)."""
import streamlit as st

from marketlens.ui import coming_soon, historical_note

st.title("📚 Personal Analytics & Strategy Library")
coming_soon("Personal Analytics & Strategy Library", [
    "Save, name, tag and reopen every strategy you build",
    "Compare backtest, simulator and forward-test results side by side",
    "Your P&L calendar heatmap, win rate, best and worst days",
    "Trade journal with notes for each trade",
])
historical_note()
