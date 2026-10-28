"""Module 2 - Option Chain & Strategy Builder.

Tab 1 "Option chain": underlying + expiry, cards (spot, ATM, PCR, max pain, ATM straddle,
expected move), the chain with ATM highlighted and in-the-money strikes shaded, OI and
change-in-OI bar charts, and "Add CE / PE to builder" buttons.
Tab 2 "Strategy builder": any mix of legs (from the chain or typed in), payoff at expiry,
max profit / loss and breakevens. Short straddle and strangle are one-click presets.
Maths: marketlens/options/chain.py and marketlens/options/payoff.py.
"""
import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketlens.broker.dhan_client import DhanError
from marketlens.broker.dhan_instruments import INDICES
from marketlens.broker.market_data import get_data_client
from marketlens.options.chain import (VIEW_COLUMNS, VIEW_LABELS, chain_summary, chain_view, lot_size_for,
                                      make_leg)
from marketlens.options.payoff import payoff_summary, price_range, strategy_payoff
from marketlens.ui import historical_note

UNDERLYINGS = list(INDICES)                       # NIFTY, BANKNIFTY, FINNIFTY, SENSEX
LEG_COLUMNS = ["side", "option_type", "strike", "premium", "lots", "lot_size"]
DEMO_BANNER = ("⚠️ DEMO DATA: the Dhan keys are not set, so this chain is made up for practice "
               "(Black-Scholes from the real spot and India VIX). Not real prices.")

st.title("🔗 Option Chain & Strategy Builder")


# Upcoming expiries (asked again every 5 minutes at most).
@st.cache_data(ttl=300, show_spinner="Loading expiries ...")
def cached_expiries(underlying: str):
    return get_data_client().get_expiry_list(underlying)


# The chain (Dhan allows one request every 3 seconds; we keep it 30 seconds).
@st.cache_data(ttl=30, show_spinner="Loading option chain ...")
def cached_chain(underlying: str, expiry: dt.date):
    return get_data_client().get_option_chain(underlying, expiry)


# Lot size per underlying (Dhan's list, or defaults when offline).
@st.cache_data(ttl=3600)
def cached_lot_size(underlying: str) -> int:
    return lot_size_for(underlying)


# The builder's legs, as edited on screen most recently.
def current_legs() -> pd.DataFrame:
    return st.session_state.get("legs_current", st.session_state.get("legs_df", pd.DataFrame(columns=LEG_COLUMNS)))


# Replace the builder's legs (the editor starts again from these rows).
def set_legs(df: pd.DataFrame):
    st.session_state["legs_df"] = df[LEG_COLUMNS].reset_index(drop=True)
    st.session_state.pop("legs_current", None)
    st.session_state.pop("legs_editor", None)


# Add one leg to the builder.
def add_leg(leg: dict):
    set_legs(pd.concat([current_legs(), pd.DataFrame([leg])[LEG_COLUMNS]], ignore_index=True))


try:
    client = get_data_client()
except DhanError as err:
    client = None
    st.error(str(err))

chain_tab, builder_tab = st.tabs(["Option chain", "Strategy builder"])

# =====================================================================
# Tab 1: option chain
# =====================================================================
with chain_tab:
    if client is None:
        st.info("The option chain needs Dhan keys or DEMO_MODE=true.")
    else:
        if client.is_demo:
            st.warning(DEMO_BANNER)
        c1, c2, c3 = st.columns(3)
        underlying = c1.selectbox("Underlying", UNDERLYINGS, key="oc_u")
        expiry = c2.selectbox("Expiry", cached_expiries(underlying), key="oc_exp",
                              format_func=lambda d: f"{d:%a %d-%b-%Y}")
        width = c3.slider("Strikes each side of ATM", 5, 20, 10, key="oc_width")
        if st.button("🔄 Refresh", key="oc_refresh"):
            cached_chain.clear()
        chain = cached_chain(underlying, expiry)
        table, spot = chain.table, chain.spot
        lot = cached_lot_size(underlying)
        st.session_state["builder_spot"] = float(spot)
        st.session_state["last_chain"] = (underlying, expiry, table, float(spot), lot)

        # ---- cards ----
        s = chain_summary(table, spot, expiry)
        m = st.columns(6)
        m[0].metric("Spot", f"{s['spot']:,.2f}")
        m[1].metric("ATM strike", f"{s['atm']:,.0f}")
        m[2].metric("PCR", f"{s['pcr']:.2f}" if s["pcr"] is not None else "-",
                    help="Total put OI / total call OI")
        m[3].metric("Max pain", f"{s['max_pain']:,.0f}" if s["max_pain"] else "-",
                    help="Expiry price at which option buyers would collect the least")
        m[4].metric("ATM straddle", f"Rs {s['straddle']:,.2f}" if s["straddle"] else "-",
                    help="ATM call + ATM put price")
        m[5].metric("Expected move", f"±{s['expected_move']:,.0f}" if s["expected_move"] else "-",
                    f"±{s['expected_move_pct']:.2f}%" if s["expected_move_pct"] else None, delta_color="off",
                    help=f"Spot x ATM IV x sqrt(days/365): about 68% chance of staying inside by expiry "
                         f"({s['days']:.1f} days left, ATM IV {s['atm_iv'] or 0:.1f}%)")

        # ---- the chain table ----
        view = chain_view(table, spot, width)
        shown = view[VIEW_COLUMNS].copy()
        shown.columns = VIEW_LABELS
        call_cols, put_cols = VIEW_LABELS[:5], VIEW_LABELS[6:]

        # Shade in-the-money sides and highlight the ATM row.
        def colour_row(row):
            flags = view.iloc[row.name]
            styles = []
            for col in shown.columns:
                if flags["is_atm"]:
                    styles.append("background-color: rgba(46,134,222,0.35); font-weight: bold")
                elif (col in call_cols and flags["ce_itm"]) or (col in put_cols and flags["pe_itm"]):
                    styles.append("background-color: rgba(250,204,21,0.15)")
                elif col == "Strike":
                    styles.append("font-weight: bold")
                else:
                    styles.append("")
            return styles
        st.dataframe(shown.style.apply(colour_row, axis=1).format(
            {c: "{:,.0f}" for c in ["Call OI", "Call OI chg", "Put OI chg", "Put OI", "Strike"]}
            | {c: "{:.2f}" for c in ["Call IV", "Call LTP", "Put LTP", "Put IV"]}
            | {c: "{:.3f}" for c in ["Call delta", "Put delta"]}, na_rep="-"),
            hide_index=True, width="stretch", height=min(40 + 35 * len(shown), 800))
        st.caption("Blue row = ATM. Yellow = in the money (calls below spot, puts above spot). "
                   f"Lot size {lot}.")

        # ---- OI and change-in-OI bars ----
        for title, ce_col, pe_col in (("Open interest by strike", "ce_oi", "pe_oi"),
                                      ("Change in open interest by strike", "ce_oi_change", "pe_oi_change")):
            fig = go.Figure([go.Bar(x=view["strike"], y=view[ce_col], name="Calls", marker_color="#dc2626"),
                             go.Bar(x=view["strike"], y=view[pe_col], name="Puts", marker_color="#16a34a")])
            fig.add_vline(x=spot, line_dash="dash", line_color="gray", annotation_text="Spot")
            fig.update_layout(title=title, barmode="group", height=330, legend=dict(orientation="h"),
                              margin=dict(t=40, b=10))
            st.plotly_chart(fig, width="stretch")

        # ---- send legs to the builder ----
        st.subheader("Add a leg to the Strategy builder")
        a1, a2, a3, a4, a5 = st.columns([2, 1.3, 1, 1, 1])
        strikes = list(view["strike"])
        pick = a1.selectbox("Strike", strikes, index=int(view["is_atm"].idxmax()), key="oc_pick",
                            format_func=lambda k: f"{k:,.0f}" + ("  (ATM)" if k == s["atm"] else ""))
        side = a2.radio("Side", ["BUY", "SELL"], horizontal=True, key="oc_side")
        lots = a3.number_input("Lots", 1, 100, 1, key="oc_lots")
        for col, opt in ((a4, "CE"), (a5, "PE")):
            col.write("")
            if col.button(f"➕ Add {opt} to builder", key=f"oc_add_{opt}"):
                add_leg(make_leg(table, underlying, expiry, pick, opt, side, lots, lot))
                st.toast(f"Added {side} {lots} lot {pick:,.0f} {opt}. Open the 'Strategy builder' tab.", icon="✅")
        st.caption(f"Legs in the builder: {len(current_legs())}")

# =====================================================================
# Tab 2: strategy builder (payoff at expiry of any legs)
# =====================================================================
with builder_tab:
    st.write("Build a strategy from legs: add them from the **Option chain** tab, use a preset, or type "
             "rows into the table (+ at the bottom adds a row). The chart shows profit / loss **at expiry**.")
    last = st.session_state.get("last_chain")
    spot = st.number_input("Spot (centre of the chart)", value=float(st.session_state.get("builder_spot", 25000.0)),
                           step=50.0, key="b_spot")

    # ---- presets (premiums from the latest chain, if one was loaded) ----
    p1, p2, p3, p4 = st.columns(4)
    if p1.button("Short straddle (ATM)", key="b_straddle") and last:
        u, e, table, s_spot, lot = last
        atm = chain_view(table, s_spot, 0)["strike"].iloc[0]
        set_legs(pd.DataFrame([make_leg(table, u, e, atm, "CE", "SELL", 1, lot),
                               make_leg(table, u, e, atm, "PE", "SELL", 1, lot)]))
    gap = p2.number_input("Strangle gap (strikes)", 1, 10, 3, key="b_gap", label_visibility="collapsed")
    if p2.button(f"Short strangle (ATM ±{gap})", key="b_strangle") and last:
        u, e, table, s_spot, lot = last
        view = chain_view(table, s_spot, gap)
        set_legs(pd.DataFrame([make_leg(table, u, e, view["strike"].iloc[-1], "CE", "SELL", 1, lot),
                               make_leg(table, u, e, view["strike"].iloc[0], "PE", "SELL", 1, lot)]))
    if p3.button("🗑️ Clear legs", key="b_clear"):
        set_legs(pd.DataFrame(columns=LEG_COLUMNS))
    if not last:
        p4.caption("Open the Option chain tab once to fill presets with live premiums.")

    edited = st.data_editor(
        st.session_state.get("legs_df", pd.DataFrame(columns=LEG_COLUMNS)), key="legs_editor",
        num_rows="dynamic", width="stretch", hide_index=True,
        column_config={
            "side": st.column_config.SelectboxColumn("Side", options=["BUY", "SELL"], required=True),
            "option_type": st.column_config.SelectboxColumn("Type", options=["CE", "PE"], required=True),
            "strike": st.column_config.NumberColumn("Strike", min_value=0.0, step=50.0, required=True),
            "premium": st.column_config.NumberColumn("Premium (Rs)", min_value=0.0, step=0.05, format="%.2f"),
            "lots": st.column_config.NumberColumn("Lots", min_value=1, step=1, required=True),
            "lot_size": st.column_config.NumberColumn("Lot size", min_value=1, step=1, required=True),
        })
    st.session_state["legs_current"] = edited
    legs = [r for r in edited.dropna(subset=["side", "option_type", "strike", "lots", "lot_size"])
            .fillna({"premium": 0.0}).to_dict("records")]

    if not legs:
        st.info("No legs yet.")
    else:
        prices = price_range(spot, pct=10)
        pnl = strategy_payoff(legs, prices)
        summ = payoff_summary(legs, prices, pnl)
        m = st.columns(4)
        m[0].metric("Max profit", "Unlimited" if summ["max_profit"] is None else f"Rs {summ['max_profit']:,.0f}")
        m[1].metric("Max loss", "Unlimited" if summ["max_loss"] is None else f"Rs {summ['max_loss']:,.0f}")
        m[2].metric("Breakevens", ", ".join(f"{b:,.0f}" for b in summ["breakevens"]) or "none")
        net = summ["net_premium"]
        m[3].metric("Net premium", f"Rs {abs(net):,.0f}", "received (credit)" if net >= 0 else "paid (debit)",
                    delta_color="normal" if net >= 0 else "inverse")

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=prices, y=np.where(pnl >= 0, pnl, 0), fill="tozeroy", name="Profit",
                                 line=dict(width=0), fillcolor="rgba(22,163,74,0.3)", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=prices, y=np.where(pnl < 0, pnl, 0), fill="tozeroy", name="Loss",
                                 line=dict(width=0), fillcolor="rgba(220,38,38,0.3)", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=prices, y=pnl, name="P&L at expiry", line=dict(color="#2e86de", width=2)))
        fig.add_hline(y=0, line_color="gray")
        fig.add_vline(x=spot, line_dash="dash", annotation_text="Spot")
        for b in summ["breakevens"]:
            fig.add_vline(x=b, line_dash="dot", line_color="orange", annotation_text=f"{b:,.0f}")
        fig.update_layout(height=460, xaxis_title="Underlying at expiry", yaxis_title="Profit / loss (Rs)",
                          legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        if summ["max_loss"] is None:
            st.warning("Loss is unlimited if the underlying rallies far past the sold calls. Always use a stop loss.")
        elif any(leg["side"] == "SELL" for leg in legs):
            st.caption("Selling options needs margin; see the cost box on the backtester pages.")
    historical_note()
