"""Module 2 - Option Chain & Strategy Builder.

Tab 1 "Option chain": underlying + expiry, cards (spot, ATM, PCR, max pain, ATM straddle,
expected move), the chain with ATM highlighted and in-the-money strikes shaded, OI and
change-in-OI bar charts, and "Add CE / PE to builder" buttons.
Tab 2 "Strategy builder": 12 templates filled from the live chain (or Custom), quick-add by
ATM +/- N / premium / delta, editable legs (CE, PE, FUT), payoff at expiry and today, net Greeks,
max profit / loss, risk-reward, probability of profit, margin, and Save / Backtest / Paper trade.
Maths: marketlens/options/chain.py, strategies.py and strike_select.py.
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
from marketlens.broker.candles import IST
from marketlens.options.strategies import (DEFAULT_IV, TEMPLATES, Leg, analyse, build_template, margin_for,
                                           payoff, save_strategy)
from marketlens.options.strike_select import (atm_offset_strike, chain_to_long, strike_for_delta,
                                              strike_for_premium)
from marketlens.ui import historical_note

UNDERLYINGS = list(INDICES)                       # NIFTY, BANKNIFTY, FINNIFTY, SENSEX
LEG_COLUMNS = ["side", "option_type", "strike", "expiry", "lots", "lot_size", "entry_price", "iv"]
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


# Replace the builder's legs with a list of Leg objects (the editor starts again from these rows).
def set_legs(legs: list):
    df = pd.DataFrame([leg.to_dict() for leg in legs], columns=LEG_COLUMNS)
    st.session_state["legs_df"] = df
    st.session_state.pop("legs_current", None)
    st.session_state.pop("legs_editor", None)


# Add one Leg to the builder, keeping the legs already there.
def add_leg(leg: Leg):
    legs, _ = rows_to_legs(current_legs())
    set_legs(legs + [leg])


# Turn the editor's rows into Leg objects; rows that are not complete are reported, not used.
def rows_to_legs(df: pd.DataFrame) -> tuple[list, list]:
    legs, problems = [], []
    for i, row in enumerate(df.to_dict("records"), start=1):
        try:
            iv = row.get("iv")
            legs.append(Leg(row["side"], row["option_type"],
                            None if row["option_type"] == "FUT" or pd.isna(row.get("strike")) else row["strike"],
                            pd.Timestamp(row["expiry"]).date(), int(row["lots"]),
                            0.0 if pd.isna(row.get("entry_price")) else row["entry_price"],
                            int(row["lot_size"]), None if iv is None or pd.isna(iv) else float(iv)))
        except Exception as err:
            if any(pd.notna(v) for v in row.values()):
                problems.append(f"Row {i} is not used: {err}")
    return legs, problems


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
# Tab 2: strategy builder
# =====================================================================
with builder_tab:
    last = st.session_state.get("last_chain")
    if last:
        b_under, b_expiry, b_table, b_spot, b_lot = last
        options = chain_to_long(b_table)
        b_atm_iv = chain_summary(b_table, b_spot, b_expiry)["atm_iv"] or DEFAULT_IV
        st.caption(f"Using the {b_under} chain for {b_expiry:%d-%b-%Y} from the Option chain tab "
                   f"(spot {b_spot:,.2f}, lot {b_lot}, ATM IV {b_atm_iv:.1f}%).")
    else:
        b_under, b_expiry, b_table, b_spot, b_lot, options, b_atm_iv = "NIFTY", None, None, 25000.0, 65, None, DEFAULT_IV
        st.info("Open the **Option chain** tab once: templates and quick-add use its live prices. "
                "You can still type legs by hand below.")

    # ---- 1. templates ----
    st.markdown("**1. Start from a template**")
    t1, t2, t3, t4 = st.columns([2.2, 1, 1, 1])
    template = t1.selectbox("Template", list(TEMPLATES), format_func=TEMPLATES.get, key="b_template")
    gap = t2.number_input("Gap (strikes from ATM)", 1, 15, 2, key="b_gap",
                          help="Strangles and iron condors: how far the sold strikes are from ATM")
    wing = t3.number_input("Wing (strikes)", 1, 15, 2, key="b_wing",
                           help="Width of spreads and of the protective legs")
    t_lots = t4.number_input("Lots", 1, 100, 1, key="b_tlots")
    far_expiry = None
    if template == "calendar_spread" and last:
        later = [e for e in cached_expiries(b_under) if e > b_expiry]
        far_expiry = st.selectbox("Far expiry (bought leg)", later, key="b_far",
                                  format_func=lambda d: f"{d:%a %d-%b-%Y}") if later else None
    if st.button("Fill legs from live chain", key="b_fill", disabled=not last):
        try:
            far = chain_to_long(cached_chain(b_under, far_expiry).table) if far_expiry else None
            set_legs(build_template(template, options, b_spot, b_expiry, b_lot, t_lots, gap, wing, far, far_expiry))
            st.session_state["b_template_used"] = template
        except ValueError as err:
            st.error(str(err))

    # ---- 2. quick add by rule (the same helpers the backtester uses) ----
    with st.expander("➕ Add one leg by rule: ATM ± N, closest premium, or closest delta", expanded=False):
        q1, q2, q3, q4, q5 = st.columns([1, 1, 1.6, 1, 1])
        q_side = q1.radio("Side", ["BUY", "SELL"], key="q_side")
        q_type = q2.radio("Type", ["CE", "PE"], key="q_type")
        q_rule = q3.radio("Strike rule", ["ATM ± N", "Premium ≈ Rs", "Delta ≈"], key="q_rule")
        q_value = q4.number_input("N / Rs / delta", value={"ATM ± N": 0.0, "Premium ≈ Rs": 50.0,
                                                           "Delta ≈": 0.20}[q_rule], key=f"q_val_{q_rule}")
        q_lots = q5.number_input("Lots", 1, 100, 1, key="q_lots")
        if st.button("Add leg", key="q_add", disabled=not last):
            try:
                if q_rule == "ATM ± N":
                    k = atm_offset_strike(options["strike"].unique(), b_spot, int(q_value))
                elif q_rule == "Premium ≈ Rs":
                    k = strike_for_premium(options, q_type, q_value)
                else:
                    k = strike_for_delta(options, q_type, q_value)
                add_leg(make_leg(b_table, b_under, b_expiry, k, q_type, q_side, q_lots, b_lot))
                st.toast(f"Added {q_side} {q_type} {k:,.0f}", icon="✅")
            except ValueError as err:
                st.error(str(err))

    # ---- 3. editable legs ----
    st.markdown("**2. Legs** (change anything; **+** at the bottom adds a row, select rows and press Delete to remove)")
    c_clear, _ = st.columns([1, 4])
    if c_clear.button("🗑️ Clear all legs", key="b_clear"):
        set_legs([])
    edited = st.data_editor(
        st.session_state.get("legs_df", pd.DataFrame(columns=LEG_COLUMNS)), key="legs_editor",
        num_rows="dynamic", width="stretch", hide_index=True,
        column_config={
            "side": st.column_config.SelectboxColumn("Side", options=["BUY", "SELL"], required=True),
            "option_type": st.column_config.SelectboxColumn("Type", options=["CE", "PE", "FUT"], required=True),
            "strike": st.column_config.NumberColumn("Strike", min_value=0.0, step=50.0, help="Empty for FUT"),
            "expiry": st.column_config.DateColumn("Expiry", format="DD-MMM-YYYY", required=True),
            "lots": st.column_config.NumberColumn("Lots", min_value=1, step=1, required=True),
            "lot_size": st.column_config.NumberColumn("Lot size", min_value=1, step=1, required=True),
            "entry_price": st.column_config.NumberColumn("Entry price (Rs)", min_value=0.0, step=0.05, format="%.2f"),
            "iv": st.column_config.NumberColumn("IV %", min_value=0.0, step=0.1, format="%.1f",
                                                help="Used for the 'today' line and Greeks"),
        })
    st.session_state["legs_current"] = edited
    legs, problems = rows_to_legs(edited)
    for problem in problems:
        st.warning(problem)

    spot = st.number_input("Spot (centre of the chart)", value=float(b_spot), step=50.0, key="b_spot")
    if not legs:
        st.info("No legs yet: pick a template, quick-add a leg, or type one into the table.")
    else:
        # ---- 4. payoff, cards and Greeks ----
        now = dt.datetime.now(IST)
        a = analyse(legs, spot, b_atm_iv, now=now)
        r1 = st.columns(5)
        r1[0].metric("Max profit", "Unlimited" if a["max_profit"] is None else f"Rs {a['max_profit']:,.0f}")
        r1[1].metric("Max loss", "Unlimited" if a["max_loss"] is None else f"Rs {a['max_loss']:,.0f}")
        r1[2].metric("Risk : reward", f"1 : {a['risk_reward']:.2f}" if a["risk_reward"] else "-",
                     help="Max profit divided by max loss (only when both are limited)")
        r1[3].metric("Probability of profit", f"{a['pop'] * 100:.1f}%",
                     help=f"Chance of a profit at the first expiry if prices move log-normally with IV {b_atm_iv:.1f}%")
        net = a["net_premium"]
        r1[4].metric("Net premium", f"Rs {abs(net):,.0f}", "received (credit)" if net >= 0 else "paid (debit)",
                     delta_color="normal" if net >= 0 else "inverse")
        g = a["greeks"]
        r2 = st.columns(5)
        r2[0].metric("Net delta", f"{g['delta']:+,.1f}", help="Like holding this many units of the underlying")
        r2[1].metric("Net gamma", f"{g['gamma']:+,.3f}", help="Change in delta per 1-point move")
        r2[2].metric("Net theta", f"Rs {g['theta']:+,.0f}/day", help="Profit (+) or loss (-) from one day passing")
        r2[3].metric("Net vega", f"Rs {g['vega']:+,.0f}", help="Profit / loss if IV rises by 1 percentage point")
        r2[4].metric("Breakevens", ", ".join(f"{b:,.0f}" for b in a["breakevens"]) or "none")

        prices, exp_pnl = a["prices"], a["expiry_pnl"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=prices, y=np.where(exp_pnl >= 0, exp_pnl, 0), fill="tozeroy", name="Profit",
                                 line=dict(width=0), fillcolor="rgba(22,163,74,0.25)", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=prices, y=np.where(exp_pnl < 0, exp_pnl, 0), fill="tozeroy", name="Loss",
                                 line=dict(width=0), fillcolor="rgba(220,38,38,0.25)", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=prices, y=exp_pnl, name="At expiry", line=dict(color="#2e86de", width=2.5)))
        fig.add_trace(go.Scatter(x=prices, y=a["today_pnl"], name="Today", line=dict(color="orange", dash="dash")))
        today_at_spot = payoff(legs, [spot], at=now)[0]
        fig.add_trace(go.Scatter(x=[spot], y=[today_at_spot], mode="markers", name=f"Spot {spot:,.0f}",
                                 marker=dict(size=12, color="black", symbol="diamond")))
        fig.add_hline(y=0, line_color="gray")
        for b in a["breakevens"]:
            fig.add_vline(x=b, line_dash="dot", line_color="orange", annotation_text=f"{b:,.0f}")
        fig.update_layout(height=480, xaxis_title=f"{b_under} price", yaxis_title="Profit / loss (Rs)",
                          legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        if a["max_loss"] is None:
            st.warning("Loss is unlimited if the underlying moves far past the sold calls. Always use a stop loss.")

        # ---- 5. margin ----
        m = margin_for(legs, b_under, spot, client if client and not client.is_demo else None)
        st.metric(f"Margin needed ({m['source']})", f"Rs {m['amount']:,.0f}")
        if m["note"]:
            st.caption(f"⚠️ {m['note']}.")

        # ---- 6. save / backtest / paper trade ----
        st.markdown("**3. Use this strategy**")
        n1, n2, n3, n4 = st.columns([2, 1, 1, 1])
        used = st.session_state.get("b_template_used", "custom")
        # The key changes with the template, so the suggested name follows the template you filled
        name = n1.text_input("Strategy name", value=f"{b_under} {TEMPLATES.get(used)}", key=f"b_name_{used}")
        handoff = {"name": name, "underlying": b_under, "spot": spot,
                   "legs": [leg.to_dict() for leg in legs], "template": st.session_state.get("b_template_used", "custom")}
        if n2.button("💾 Save to Strategy Library", key="b_save"):
            try:
                strikes = options["strike"].unique() if options is not None else [l.strike for l in legs if l.strike]
                sid = save_strategy(name, legs, b_under, spot, strikes, handoff["template"])
                st.success(f"Saved '{name}' (id {sid}). It will appear in module 10, Strategy Library.")
            except Exception as err:
                st.error(f"Could not save: {err}")
        if n3.button("🧪 Backtest this", key="b_backtest"):
            st.session_state["handoff"] = {**handoff, "to": "backtest"}
            st.switch_page("dashboards/options_backtester.py")
        if n4.button("📝 Paper trade this", key="b_paper"):
            st.session_state["handoff"] = {**handoff, "to": "paper"}
            st.switch_page("dashboards/forward_test.py")
    historical_note()
