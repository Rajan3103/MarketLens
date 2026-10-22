"""Option Chain dashboard (planned). Requirements FR-02 and FR-09."""
import streamlit as st

from marketlens.ui import coming_soon

st.title("🔗 Option Chain")
coming_soon("Option Chain", [
    "Live Nifty and Bank Nifty option chain from the Dhan API",
    "LTP, open interest, IV and Greeks for every strike",
    "ATM strike, put-call ratio (PCR) and max pain",
    "Download the chain as CSV",
])
