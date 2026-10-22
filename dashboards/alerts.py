"""Alerts & Telegram (planned). Requirements FR-27 to FR-29."""
import streamlit as st

from marketlens.ui import coming_soon

st.title("🔔 Alerts & Telegram")
coming_soon("Alerts and Telegram bot", [
    "Telegram messages for signals, price levels and order fills",
    "Bot commands: /status, /positions, /pnl, /stop",
    "Add, list and delete price alerts",
])
