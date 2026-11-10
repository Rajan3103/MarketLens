"""Module 8 - Momentum Engine Backtest vs Nifty. Requirements FR-13 to FR-15.

The page itself is in the web app (frontend/app/momentum-engine): backtest form with progress, current
ranks and rebalance history. This Streamlit page links there, and opens a strategy sent from the Strategy
Library (module 10) on the web page. The engine is marketlens/momentum/portfolio_bt.py.
"""
import streamlit as st

from marketlens import config
from marketlens.ui import help_note, historical_note

st.title("🚀 Momentum Engine Backtest vs Nifty")
help_note("What this page does: links to the Momentum Engine in the web app (top-N Nifty 50 / 200 stocks by "
          "momentum, rebalanced monthly or weekly, against Nifty 50).\n"
          "How to read it: lookbacks are in trading days (63 ≈ 3 months), weights in %, values in Rs after "
          "costs and tax.")
address = f"{config.FRONTEND_URL}/momentum-engine"

opened = st.session_state.get("handoff") or {}
if opened.get("to") == "momentum":
    st.session_state.pop("handoff")
    link = f"{address}?strategy={opened['strategy_id']}"
    st.success(f"Strategy **{opened['name']}** (id {opened['strategy_id']}) is ready to open in the web app.")
    st.link_button("🚀 Open it in the Momentum Engine", link, type="primary")
    st.caption(f"Address: {link}")

st.markdown(
    "This module runs in the **web app**: rank Nifty 50 / Nifty 200 stocks by 3, 6 and 12-month momentum, hold "
    "the top N (keeping a stock while it stays in the top M), rebalance monthly or weekly with equal or "
    "inverse-volatility weights, and compare the result after costs and tax with Nifty 50 buy-and-hold.")
st.link_button("Open the Momentum Engine", address)
st.caption("Start it with two terminals: `python -m marketlens.api` and, in frontend/, `npm run dev`.")
historical_note()
