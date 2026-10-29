"""Module 5 - Option Simulator. Similar in spirit to StockMock.

Replay a stored past day minute by minute and trade it with virtual money. Only what was known at
the replay minute is shown. The clock lives in st.session_state; while playing, only the part of
the page inside the fragment (clock, charts, chain, positions) redraws every second.
"""
import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketlens.simulator import replay as rp
from marketlens.ui import cost_settings_inputs, historical_note, key_notice

UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]
CHAIN_COLUMNS = ["ce_oi", "ce_delta", "ce_iv", "ce_ltp", "strike", "pe_ltp", "pe_iv", "pe_delta", "pe_oi"]
CHAIN_LABELS = {"ce_oi": "CE OI", "ce_delta": "CE Δ", "ce_iv": "CE IV", "ce_ltp": "CE LTP", "strike": "Strike",
                "pe_ltp": "PE LTP", "pe_iv": "PE IV", "pe_delta": "PE Δ", "pe_oi": "PE OI"}


# Stored days for an underlying (cached for a minute).
@st.cache_data(ttl=60, show_spinner=False)
def cached_days(underlying: str) -> list:
    return rp.available_days(underlying)


# Stored expiries for a day (cached for a minute).
@st.cache_data(ttl=60, show_spinner=False)
def cached_expiries(underlying: str, day: dt.date) -> list:
    return rp.available_expiries(underlying, day)


# ---------------------------------------------------------------- button actions (run before the redraw)

# Load the chosen day and start a new session at the chosen time (reads the boxes when clicked).
def start_session(costs):
    s = st.session_state
    try:
        sim = rp.SimSession(rp.load_day(s["s_underlying"], s["s_day"], s["s_expiry"]), s["s_underlying"],
                            s["s_lot"] or None, costs, s["s_capital"])
        sim.jump(s["s_start"])
        st.session_state["sim"] = sim
        st.session_state["sim_slider"] = sim.clock.time()
    except Exception as err:
        st.session_state["sim_msg"] = ("error", f"Could not load the day: {err}")


# Run one action on the session and keep its error message for the page.
def act(action, *args):
    try:
        action(*args)
    except ValueError as err:
        st.session_state["sim_msg"] = ("error", str(err))


# Play / pause (the whole page reruns so the fragment can start or stop its timer).
def toggle_play():
    clock = st.session_state["sim"].clock
    clock.pause() if clock.playing else clock.play()


# Step the clock forward.
def step(n: int):
    act(st.session_state["sim"].advance, n)


# Jump to the time in the "Jump to" box or on the slider.
def jump(key: str):
    act(st.session_state["sim"].jump, st.session_state[key])


# Change the replay speed.
def set_speed():
    st.session_state["sim"].clock.set_speed(st.session_state["sim_speed"])


# Place the order from the order boxes.
def place_order():
    s = st.session_state
    act(s["sim"].place, s["o_side"], s["o_strike"], s["o_type"], s["o_lots"], s["o_sl"] or None, s["o_target"] or None)


# Exit one position or all of them.
def exit_position(pos_id: int | None):
    sim = st.session_state["sim"]
    act(sim.exit_all) if pos_id is None else act(sim.exit, pos_id)


# Save the session to the database.
def save():
    try:
        sid = rp.save_session(st.session_state["sim"], st.session_state.get("sim_notes", ""))
        st.session_state["sim_msg"] = ("success", f"Saved as session {sid}. It is listed under Past sessions.")
    except Exception as err:
        st.session_state["sim_msg"] = ("error", f"Could not save: {err}")


# ---------------------------------------------------------------- charts

# Index candles so far (5-minute candles built from the minutes that have finished).
def spot_chart(sim: rp.SimSession) -> go.Figure:
    c = sim.spot_candles(every=5)
    fig = go.Figure(go.Candlestick(x=c["ts"], open=c["open"], high=c["high"], low=c["low"], close=c["close"],
                                   name=sim.underlying))
    day = sim.day.day
    fig.update_xaxes(range=[dt.datetime.combine(day, dt.time(9, 15)), dt.datetime.combine(day, dt.time(15, 30))],
                     rangeslider_visible=False)
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10), title=f"{sim.underlying} (5-min, so far)")
    return fig


# Live P&L after costs, one point per replayed minute.
def pnl_chart(sim: rp.SimSession) -> go.Figure:
    curve = sim.curve_frame()
    fig = go.Figure(go.Scatter(x=curve["time"], y=curve["pnl"], name="P&L", line=dict(color="#2a78d6", width=2),
                               fill="tozeroy", fillcolor="rgba(42,120,214,0.12)"))
    fig.add_hline(y=0, line_color="gray")
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10), title="P&L (Rs, after costs)",
                      yaxis_tickprefix="Rs ")
    return fig


# Mini payoff of the open positions: at expiry and now (Black-Scholes), with the spot marked.
def payoff_chart(sim: rp.SimSession) -> go.Figure | None:
    from marketlens.options.strategies import payoff
    legs = sim.open_legs()
    if not legs:
        return None
    spot = sim.spot()
    prices = np.linspace(spot * 0.96, spot * 1.04, 161)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=prices, y=payoff(legs, prices, at="expiry"), name="At expiry",
                             line=dict(color="#2a78d6", width=2)))
    fig.add_trace(go.Scatter(x=prices, y=payoff(legs, prices, at=sim.now()), name="Now",
                             line=dict(color="#eb6834", width=2, dash="dash")))
    fig.add_vline(x=spot, line_color="gray", line_dash="dot", annotation_text=f"spot {spot:,.0f}")
    fig.add_hline(y=0, line_color="gray")
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10), title="Payoff of open positions",
                      legend=dict(orientation="h"))
    return fig


# ---------------------------------------------------------------- page

st.title("⏪ Option Simulator")
st.caption("Replay a past day minute by minute and practise trading it with virtual money.")
key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
           "The Options Data Downloader stores labelled DEMO days until then.")

msg = st.session_state.pop("sim_msg", None)
if msg:
    (st.error if msg[0] == "error" else st.success)(msg[1])

# A day sent here from the Options Backtester (module 4)
handoff = st.session_state.get("sim_handoff")
if handoff:
    with st.expander(f"From the Options Backtester: {handoff['day']:%a %d-%b-%Y} · {handoff['name']}", expanded=False):
        st.caption("The backtest's trades on that day (compare them with your own):")
        st.dataframe(handoff["trades"], hide_index=True, width="stretch")

sim: rp.SimSession | None = st.session_state.get("sim")

# ---- 1. choose a day ----
with st.expander("1. Choose a day to replay", expanded=sim is None):
    a = st.columns([1, 1.3, 1.3, 1, 1, 1])
    default_u = handoff["underlying"] if handoff else "NIFTY"
    underlying = a[0].selectbox("Underlying", UNDERLYINGS, index=UNDERLYINGS.index(default_u), key="s_underlying")
    try:
        days = cached_days(underlying)
    except Exception as err:
        days = []
        st.error(f"Could not read the database: {err}")
    if not days:
        st.warning(f"No {underlying} option minutes are stored yet. Download some days first (module 3).")
        if st.button("📥 Open the Options Data Downloader", key="s_to_downloader"):
            st.switch_page("dashboards/options_downloader.py")
    else:
        want = handoff["day"] if handoff and handoff["day"] in days else days[-1]
        day = a[1].selectbox("Day", days, index=days.index(want), format_func=lambda d: f"{d:%a %d-%b-%Y}", key="s_day")
        expiries = cached_expiries(underlying, day)
        want_e = handoff["expiry"] if handoff and handoff["expiry"] in expiries else expiries[0]
        a[2].selectbox("Expiry", expiries, index=expiries.index(want_e),
                                format_func=lambda d: f"{d:%a %d-%b-%Y}", key="s_expiry")
        a[3].time_input("Start at", dt.time(9, 20), step=60, key="s_start")
        a[4].number_input("Capital (Rs)", 10_000.0, 1e9, 500_000.0, step=50_000.0, key="s_capital")
        a[5].number_input("Lot size (0 = today's)", 0, 10_000, 0, key="s_lot")
        costs = cost_settings_inputs("simulator")
        st.button("▶️ Load this day" if sim is None else "🔄 Start a new session with this day", type="primary",
                  on_click=start_session, args=(costs,))

if sim is None:
    with st.expander("Past sessions"):
        try:
            st.dataframe(rp.list_sessions(), hide_index=True, width="stretch")
        except Exception as err:
            st.caption(f"Could not read past sessions: {err}")
    historical_note()
    st.stop()

if sim.day.is_demo:
    st.warning("DEMO DATA: this day was made up for practice (Dhan keys not set when it was downloaded).")
st.markdown(f"**{sim.underlying}** · {sim.day.day:%a %d-%b-%Y} · expiry {sim.day.expiry:%d-%b-%Y} · "
            f"lot {sim.lot_size}")
playing = sim.clock.playing


# ---- 2. the moving parts: redraws every second while playing ----
@st.fragment(run_every=1.0 if playing else None)
def live():
    sim = st.session_state["sim"]
    due = sim.clock.due()
    if due:
        sim.advance(due)
    if sim.clock.playing != playing:               # Play / Pause pressed, or 15:30 reached:
        st.rerun()                                 # rerun the whole page to start / stop the timer

    c = st.columns([0.9, 0.6, 0.6, 0.9, 1.0, 0.5, 3.0])
    c[0].button("⏸ Pause" if sim.clock.playing else "▶️ Play", on_click=toggle_play, key="c_play",
                disabled=sim.clock.finished, width="stretch")
    c[1].button("+1 min", on_click=step, args=(1,), key="c_1", disabled=sim.clock.finished, width="stretch")
    c[2].button("+5 min", on_click=step, args=(5,), key="c_5", disabled=sim.clock.finished, width="stretch")
    c[3].selectbox("Speed", rp.SPEEDS, index=rp.SPEEDS.index(sim.clock.speed), key="sim_speed", on_change=set_speed,
                   format_func=lambda s: f"{s}x ({s} min/s)", label_visibility="collapsed")
    c[4].time_input("Jump to", sim.clock.time(), step=60, key="c_jump_to", label_visibility="collapsed")
    c[5].button("Go", on_click=jump, args=("c_jump_to",), key="c_go")
    st.session_state["sim_slider"] = sim.clock.time()
    c[6].slider("Time", min_value=dt.time(9, 16), max_value=dt.time(15, 30), step=dt.timedelta(minutes=1),
                key="sim_slider", on_change=jump, args=("sim_slider",), label_visibility="collapsed")

    m = st.columns(5)
    m[0].metric("Replay time", f"{sim.clock.time():%H:%M}", "finished" if sim.clock.finished else
                ("playing" if sim.clock.playing else "paused"), delta_color="off")
    m[1].metric(f"{sim.underlying} spot", f"{sim.spot():,.2f}")
    m[2].metric("P&L (after costs)", f"Rs {sim.pnl():,.0f}")
    m[3].metric("Open positions", sum(p.is_open for p in sim.positions))
    m[4].metric("Orders", len(sim.positions) + sum(not p.is_open for p in sim.positions))

    g = st.columns(3)
    g[0].plotly_chart(spot_chart(sim), width="stretch", key="ch_spot")
    g[1].plotly_chart(pnl_chart(sim), width="stretch", key="ch_pnl")
    payoff = payoff_chart(sim)
    if payoff is None:
        g[2].info("The payoff of your open positions appears here.")
    else:
        g[2].plotly_chart(payoff, width="stretch", key="ch_payoff")

    left, right = st.columns([1.5, 1])
    chain = sim.chain()
    spot = sim.spot()
    with left:
        st.markdown(f"**Option chain at {sim.clock.time():%H:%M}** (last traded prices; delta from IV)")
        if chain.empty:
            st.info("No option prices yet at this minute.")
        else:
            near = chain.iloc[(chain["strike"] - spot).abs().argsort()[:21]].sort_values("strike")
            st.dataframe(near[CHAIN_COLUMNS].rename(columns=CHAIN_LABELS), hide_index=True, width="stretch",
                         height=420, column_config={
                             "CE IV": st.column_config.NumberColumn(format="%.1f"),
                             "PE IV": st.column_config.NumberColumn(format="%.1f"),
                             "CE Δ": st.column_config.NumberColumn(format="%.2f"),
                             "PE Δ": st.column_config.NumberColumn(format="%.2f"),
                             "CE OI": st.column_config.NumberColumn(format="compact"),
                             "PE OI": st.column_config.NumberColumn(format="compact")})
    with right.container(border=True):
        st.markdown("**New order** (at this minute's price)")
        strikes = list(chain["strike"]) if not chain.empty else []
        o = st.columns(2)
        o[0].radio("Side", ["BUY", "SELL"], key="o_side", horizontal=True)
        o[1].radio("Type", ["CE", "PE"], key="o_type", horizontal=True)
        if strikes:
            atm = min(strikes, key=lambda k: abs(k - spot))
            if st.session_state.get("o_strike") not in strikes:
                st.session_state["o_strike"] = atm
            st.selectbox("Strike", strikes, key="o_strike", format_func=lambda k: f"{k:,.0f}" + (" (ATM)" if k == atm else ""))
        p = st.columns(3)
        p[0].number_input("Lots", 1, 500, 1, key="o_lots")
        p[1].number_input("SL (points)", 0.0, 10_000.0, 0.0, step=5.0, key="o_sl", help="0 = no stop loss")
        p[2].number_input("Target (points)", 0.0, 10_000.0, 0.0, step=5.0, key="o_target", help="0 = no target")
        if strikes:
            price = sim.quote(st.session_state["o_strike"], st.session_state["o_type"])
            st.caption(f"Last price {price:,.2f} · {st.session_state['o_lots'] * sim.lot_size} units")
        st.button("Place order", type="primary", on_click=place_order, key="o_place",
                  disabled=sim.clock.finished or not strikes, width="stretch")

    st.markdown("**Positions**")
    table = sim.positions_frame()
    if table.empty:
        st.caption("No positions yet.")
    else:
        st.dataframe(table, hide_index=True, width="stretch")
        open_ids = [p.id for p in sim.positions if p.is_open]
        e = st.columns([1, 1, 4])
        if open_ids:
            e[0].selectbox("Position", open_ids, key="x_id", label_visibility="collapsed",
                           format_func=lambda i: f"#{i} {sim.positions[i - 1].symbol}")
            e[1].button("Exit position", on_click=lambda: exit_position(st.session_state["x_id"]), key="x_one")
            e[2].button("Exit all", on_click=exit_position, args=(None,), key="x_all")

    if sim.clock.finished:
        s = sim.summary()
        st.success("The day is over. Open positions were closed at 15:30.")
        k = st.columns(5)
        k[0].metric("Final P&L", f"Rs {s['pnl']:,.0f}")
        k[1].metric("Trades", s["trades"])
        k[2].metric("Win rate", f"{s['win_rate_pct']:.0f}%" if s["win_rate_pct"] is not None else "-")
        k[3].metric("Costs", f"Rs {s['costs']:,.0f}")
        k[4].metric("Best / worst P&L", f"Rs {s['best_pnl']:,.0f} / {s['worst_pnl']:,.0f}")

    v = st.columns([3, 1])
    v[0].text_input("Notes for this session", key="sim_notes", label_visibility="collapsed",
                    placeholder="Notes for this session (optional)")
    v[1].button("💾 Save session" if not sim.saved_id else f"💾 Save again (session {sim.saved_id})",
                on_click=save, key="sim_save", width="stretch")


live()

with st.expander("Past sessions"):
    try:
        past = rp.list_sessions()
        st.dataframe(past, hide_index=True, width="stretch")
        if len(past):
            pick = st.selectbox("Show the orders of session", list(past["id"]), key="past_id")
            st.dataframe(rp.session_orders(pick), hide_index=True, width="stretch")
    except Exception as err:
        st.caption(f"Could not read past sessions: {err}")
historical_note()
