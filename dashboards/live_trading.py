"""Module 11 - Live Algo Trading & Bot Monitor (planned)."""
import streamlit as st

from marketlens import config
from marketlens.ui import coming_soon, key_notice, trading_mode_banner

st.title("🤖 Live Algo Trading & Bot Monitor")
trading_mode_banner()
key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
           "Only paper (virtual) orders are possible until then.")
st.caption(f"Safety limits from .env: stop after a loss of Rs {config.MAX_DAILY_LOSS_RS:,.0f} "
           f"in a day, at most {config.MAX_ORDERS_PER_SECOND} orders per second.")
handoff = st.session_state.get("handoff")
if handoff and handoff.get("to") == "live":
    with st.container(border=True):
        st.success(f"Received from the Strategy Library: **{handoff['name']}** (id {handoff['strategy_id']}).")
        st.caption("Live deployment is the next part of this module. Until then, run it with virtual money on "
                   "page 6 (Forward Test) and compare it with its backtest.")
        if st.button("Clear", key="live_clear_handoff"):
            st.session_state.pop("handoff")
            st.rerun()
coming_soon("Live Algo Trading & Bot Monitor", [
    "Turn strategy signals into orders on Dhan (dhanhq library)",
    "Paper mode by default; real orders only when TRADING_MODE=live",
    "Daily loss limit with a kill switch that closes all positions",
    "Bot monitor: which strategies are running, last signal, errors and logs",
    "Open positions, order book and today's P&L",
])
