"""Module 2 - Option Chain & Strategy Builder.

Tab 1 (planned): live option chain from Dhan.
Tab 2 (ready): payoff at expiry for a short straddle or strangle. Works offline:
you type the premiums yourself for now. Later they will come from the live chain.
"""
import numpy as np
import plotly.graph_objects as go
import streamlit as st

from marketlens.ui import coming_soon, historical_note, key_notice

st.title("🔗 Option Chain & Strategy Builder")
chain_tab, builder_tab = st.tabs(["Option chain", "Strategy builder"])

# ---- Tab 1: option chain (planned) ----
with chain_tab:
    key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
               "The option chain will show DEMO data until then.")
    coming_soon("Option Chain", [
        "Live Nifty, Bank Nifty and stock option chain from the Dhan API",
        "LTP, open interest, change in OI, IV and Greeks for every strike",
        "ATM strike, put-call ratio (PCR) and max pain",
        "Click strikes to add legs to the strategy builder",
        "Download the chain as CSV",
    ])

# ---- Tab 2: strategy builder (short straddle / strangle payoff) ----
with builder_tab:
    st.write("A **short straddle** sells a call and a put at the same strike. "
             "A **short strangle** sells an out-of-the-money call and put. "
             "Both earn the premium if the index stays in a range.")

    strategy = st.radio("Strategy", ["Short straddle", "Short strangle"], horizontal=True)
    c1, c2, c3 = st.columns(3)
    spot = c1.number_input("Nifty spot", value=25000, step=50)
    lot = c2.number_input("Lot size", value=75, step=25)
    lots = c3.number_input("Lots", value=1, min_value=1)

    if strategy == "Short straddle":
        call_strike = put_strike = st.number_input("Strike (ATM)", value=25000, step=50)
    else:
        s1, s2 = st.columns(2)
        put_strike = s1.number_input("Put strike (below spot)", value=24700, step=50)
        call_strike = s2.number_input("Call strike (above spot)", value=25300, step=50)

    p1, p2 = st.columns(2)
    call_prem = p1.number_input("Call premium received (Rs)", value=150.0)
    put_prem = p2.number_input("Put premium received (Rs)", value=140.0)

    # Index levels at expiry from -10% to +10% of spot
    prices = np.linspace(spot * 0.9, spot * 1.1, 400)
    # Seller keeps the premium but pays out anything the option is worth at expiry
    call_pnl = call_prem - np.maximum(prices - call_strike, 0)
    put_pnl = put_prem - np.maximum(put_strike - prices, 0)
    pnl = (call_pnl + put_pnl) * lot * lots

    total_prem = call_prem + put_prem
    low_be, high_be = put_strike - total_prem, call_strike + total_prem

    m1, m2, m3 = st.columns(3)
    m1.metric("Max profit (Rs)", f"{total_prem * lot * lots:,.0f}")
    m2.metric("Lower breakeven", f"{low_be:,.0f}")
    m3.metric("Upper breakeven", f"{high_be:,.0f}")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=prices, y=pnl, name="P&L at expiry", fill="tozeroy"))
    fig.add_hline(y=0, line_color="gray")
    fig.add_vline(x=spot, line_dash="dash", annotation_text="Spot")
    fig.update_layout(height=450, xaxis_title="Nifty at expiry", yaxis_title="Profit / loss (Rs)")
    st.plotly_chart(fig, width="stretch")
    st.warning("Loss is unlimited if the index moves far beyond a breakeven. Always use a stop loss.")
    historical_note()
