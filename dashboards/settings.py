"""Settings & Status: shows which keys are filled in, without showing the keys.

Requirements FR-32 and NFR-07.
"""
import pandas as pd
import streamlit as st

from marketlens import config
from marketlens.ui import trading_mode_banner

st.title("⚙️ Settings & Status")
st.info(
    "🔒 Keys are read from the `.env` file on this computer. "
    "`.env` is listed in `.gitignore`, so it is **never uploaded** to GitHub. "
    "Copy `.env.example` to `.env` to start, and restart the app after editing it."
)

# ---- 1. Trading mode ----
st.subheader("Trading mode")
trading_mode_banner()
if config.AUTO_TRADE:
    st.warning("AUTO_TRADE is on: strategies may place orders without asking you.")

# ---- 2. Keys: set or not (the values themselves are never shown) ----
st.subheader("API keys")
rows = [
    {"Key": name, "Status": "✅ set" if done else "❌ not set", "What it unlocks": unlocks}
    for name, done, unlocks in config.key_status()
]
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
missing = sum(1 for _, done, _ in config.key_status() if not done)
if missing:
    st.caption(f"{missing} key(s) not set. Pages that need them show demo data or a 'key not set' message.")

# ---- 3. Other settings ----
st.subheader("Other settings")
others = {
    "TRADING_MODE": config.TRADING_MODE,
    "AUTO_TRADE": config.AUTO_TRADE,
    "MAX_DAILY_LOSS_RS": f"Rs {config.MAX_DAILY_LOSS_RS:,.0f}",
    "MAX_ORDERS_PER_SECOND": config.MAX_ORDERS_PER_SECOND,
    "DATA_SOURCE": config.DATA_SOURCE,
    "DEMO_MODE": config.DEMO_MODE,
    "DATABASE_URL": config.safe_database_url(),  # password hidden
}
st.dataframe(pd.DataFrame({"Setting": others.keys(), "Value": [str(v) for v in others.values()]}),
             hide_index=True, width="stretch")
