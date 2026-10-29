"""Module 6 - Forward Test / Paper Trading (planned)."""
import pandas as pd
import streamlit as st

from marketlens.costs import round_trip_costs
from marketlens.ui import cost_settings_inputs, coming_soon, historical_note, trading_mode_banner

st.title("📝 Forward Test / Paper Trading")

# A strategy sent here from the Strategy builder (module 2) or the Options Backtester (module 4)
handoff = st.session_state.get("handoff")
if handoff and handoff.get("to") == "paper":
    st.success(f"Received from the {handoff.get('source', 'Strategy builder')}: **{handoff['name']}** "
               f"({handoff['underlying']}, {len(handoff['legs'])} legs). Paper trading (coming soon) will run it with virtual money.")
    st.dataframe(pd.DataFrame(handoff["legs"]), hide_index=True, width="stretch")
trading_mode_banner()
coming_soon("Forward Test / Paper Trading", [
    "Run a saved strategy on today's live market with virtual money",
    "Virtual order book, positions and P&L that update during the day",
    "Compare forward-test results with the strategy's backtest",
    "Daily P&L log saved to the database",
])
# Cost settings this page will use (change them in the box)
costs = cost_settings_inputs("forward_test")
example = round_trip_costs("options", 200, 200, 65, costs, entry_side="SELL")
st.caption(f"Example with these settings: selling 1 NIFTY lot (65) at Rs 200 and buying it "
           f"back at Rs 200 costs Rs {example:,.2f}.")
historical_note()
