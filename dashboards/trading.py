"""Orders & Positions (planned). Requirements FR-22 to FR-26."""
import streamlit as st

from marketlens import config
from marketlens.ui import coming_soon

st.title("🤖 Orders & Positions")
mode = config.TRADING_MODE
if mode == "live":
    st.error("Mode: LIVE. Orders use real money.")
else:
    st.success("Mode: PAPER. Orders are practice only.")

coming_soon("Order execution", [
    "Turn strategy signals into orders on Dhan",
    "Paper mode by default, live mode only when you switch it on",
    "Daily loss limit with a kill switch",
    "Open positions, order book and P&L",
])
