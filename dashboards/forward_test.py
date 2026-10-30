"""Module 6 - Forward Test / Paper Trading.

Pick a strategy from the Strategy Library and run it on today's live market with virtual money.
The strategy runs in a background process (python -m marketlens.trading.forward --strategy ID),
so it keeps going when this page or the browser is closed. This page only starts / stops it and
shows its status, the open legs, today's trades and the day-by-day history next to the backtest.
"""
import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketlens import config
from marketlens.backtest import options_bt as ob
from marketlens.trading import forward as fw
from marketlens.ui import historical_note, key_notice, trading_mode_banner

st.title("📝 Forward Test / Paper Trading")
st.caption("Run a saved strategy on today's live market with virtual money, minute by minute.")
trading_mode_banner()
key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"], "Forward tests run on DEMO prices until then.")

# ---- a strategy sent here from the Options Backtester (module 4) or the Strategy builder (module 2) ----
handoff = st.session_state.get("handoff")
if handoff and handoff.get("to") == "paper":
    with st.container(border=True):
        st.success(f"Received from the {handoff.get('source', 'Strategy builder')}: **{handoff['name']}**")
        if handoff.get("settings"):
            h1, h2 = st.columns([3, 1])
            name = h1.text_input("Save it to the Strategy Library as", handoff["name"][:80], key="f_handoff_name")
            if h2.button("💾 Save and select", key="f_handoff_save"):
                try:
                    sid = ob.save_backtest_strategy(name, ob.settings_from_params(handoff["settings"]))
                    st.session_state["f_pick"] = sid
                    st.session_state.pop("handoff")
                    st.rerun()
                except Exception as err:
                    st.error(f"Could not save: {err}")
        else:
            st.info("Strategy-builder legs have exact strikes. Open them in the Options Backtester first "
                    "(\"Backtest this\"), then use \"Start forward test\" there, so the legs become rules.")

try:
    strategies = fw.list_strategies()
except Exception as err:
    strategies = pd.DataFrame(columns=["id", "name", "kind", "underlying"])
    st.error(f"Could not read the Strategy Library: {err}")

run_tab, history_tab, new_tab = st.tabs(["▶️ Run", "📅 History vs backtest", "➕ Quick indicator strategy"])

# ---- quick indicator strategy (until module 7 builds richer ones) ----
with new_tab:
    st.caption("A simple rule on one stock or index. Signals use finished candles only; the position is "
               "closed at the exit time.")
    q = st.columns(4)
    q_name = q[0].text_input("Name", "EMA 9/21 NIFTYBEES", key="q_name")
    q_symbol = q[1].text_input("Symbol (NSE)", "NIFTYBEES", key="q_symbol")
    q_rule = q[2].selectbox("Rule", list(fw.INDICATOR_RULES), format_func=fw.INDICATOR_RULES.get, key="q_rule")
    q_qty = q[3].number_input("Quantity", 1, 100_000, 10, key="q_qty")
    r = st.columns(4)
    q_tf = r[0].selectbox("Candle (minutes)", [1, 5, 15, 25, 60], index=1, key="q_tf")
    if q_rule == "ema_cross":
        q_fast = r[1].number_input("Fast EMA", 2, 200, 9, key="q_fast")
        q_slow = r[2].number_input("Slow EMA", 3, 400, 21, key="q_slow")
        extra = {"fast": q_fast, "slow": q_slow}
    else:
        q_buy = r[1].number_input("Buy when RSI rises above", 5.0, 50.0, 30.0, key="q_rsi_buy")
        q_sell = r[2].number_input("Sell when RSI falls below", 50.0, 95.0, 70.0, key="q_rsi_sell")
        extra = {"rsi_buy": q_buy, "rsi_sell": q_sell}
    q_short = r[3].checkbox("Allow short selling", False, key="q_short")
    if st.button("💾 Save indicator strategy", key="q_save"):
        try:
            strategy = fw.IndicatorStrategy(q_symbol, int(q_qty), q_rule, timeframe=int(q_tf),
                                            allow_short=q_short, **extra)
            sid = fw.save_indicator_strategy(q_name, strategy)
            st.session_state["f_pick"] = sid
            st.success(f"Saved '{q_name}' (id {sid}). Pick it in the Run tab.")
        except Exception as err:
            st.error(str(err))

# ---- run ----
with run_tab:
    if strategies.empty:
        st.info("No strategies to run yet. Save one from the Options Backtester (\"Save to Strategy Library\"), "
                "or make a quick indicator strategy in the third tab.")
        historical_note()
        st.stop()
    ids = list(strategies["id"])
    pick = st.session_state.get("f_pick")
    labels = {row.id: f"{row.name} ({row.kind}, {row.underlying})" for row in strategies.itertuples()}
    c = st.columns([3, 1.2, 0.8, 0.8])
    strategy_id = c[0].selectbox("Strategy", ids, index=ids.index(pick) if pick in ids else 0,
                                 format_func=labels.get, key="f_strategy")
    capital = c[1].number_input("Virtual capital (Rs)", 10_000.0, 1e9, float(config.PAPER_CAPITAL),
                                step=50_000.0, key="f_capital")
    try:
        now_status = fw.status(strategy_id)
    except Exception as err:
        now_status = None
        st.error(f"Could not read the status: {err}")
    running = bool(now_status and now_status["alive"])
    if c[2].button("▶️ Start", type="primary", disabled=running, key="f_start", width="stretch"):
        pid = fw.start_process(strategy_id, capital)
        st.success(f"Started in the background (process {pid}). The first status appears within a minute. "
                   f"Log: logs/forward_{strategy_id}.log")
    if c[3].button("⏹ Stop", disabled=not running, key="f_stop", width="stretch"):
        if fw.request_stop(strategy_id):
            st.warning("Stop requested: open positions are closed at the next minute.")
    st.caption(f"Or from a terminal: `python -m marketlens.trading.forward --strategy {strategy_id}` "
               "(add `--once` for a single cycle).")

    # The status panel refreshes itself every 10 seconds.
    @st.fragment(run_every=10)
    def live_status():
        s = fw.status(strategy_id)
        if s is None:
            st.info("Not started yet.")
            return
        age = s["heartbeat_age"]
        running_now = s["alive"]
        m = st.columns(4)
        m[0].metric("Status", s["state_text"], "running" if running_now else s["status"], delta_color="off")
        m[1].metric("Today's P&L (after costs)", f"Rs {s['pnl']:,.0f}")
        m[2].metric("Market time", s["params"].get("market_time", "-"), f"{s['day']:%d-%b-%Y}" if s["day"] else None,
                    delta_color="off")
        m[3].metric("Last check-in", f"{age:,.0f} s ago" if age is not None else "-")
        if s["message"]:
            (st.error if s["state"] == "error" else st.info)(s["message"])
        if not running_now and s["status"] == "running" and age is not None:
            st.warning("No check-in for a while: the background process is not running. Press Start.")
        legs, trades = s["params"].get("legs", []), s["params"].get("trades", [])
        st.markdown("**Open legs**")
        if legs:
            st.dataframe(pd.DataFrame(legs), hide_index=True, width="stretch")
        else:
            st.caption("None.")
        st.markdown("**Today's trades** (paper fills next to the backtest rule's prices)")
        if trades:
            table = pd.DataFrame(trades)
            if "exit_reason" in table:
                table["exit_reason"] = table["exit_reason"].map(lambda r: ob.EXIT_REASONS.get(r, r))
            st.dataframe(table, hide_index=True, width="stretch")
        else:
            st.caption("None yet.")

    live_status()

# ---- history ----
with history_tab:
    hist_id = st.selectbox("Strategy", list(strategies["id"]), format_func=lambda i: labels.get(i, i) if not
                           strategies.empty else i, key="f_hist_strategy") if not strategies.empty else None
    if hist_id is not None:
        try:
            table = fw.history(hist_id)
        except Exception as err:
            table = None
            st.error(f"Could not build the history: {err}")
        if table is None or table.empty:
            st.info("No forward-test days yet.")
        else:
            fig = go.Figure()
            fig.add_bar(x=table["date"], y=table["forward_pnl"], name="Forward test (paper)", marker_color="#2a78d6")
            fig.add_bar(x=table["date"], y=pd.to_numeric(table["backtest_pnl"], errors="coerce"),
                        name="Backtest, same days", marker_color="#eb6834")
            fig.update_layout(barmode="group", height=320, yaxis_title="Daily P&L (Rs)",
                              legend=dict(orientation="h"), margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig, width="stretch")
            st.dataframe(table, hide_index=True, width="stretch", column_config={
                "forward_pnl": st.column_config.NumberColumn("Forward P&L", format="%.0f"),
                "backtest_pnl": st.column_config.NumberColumn("Backtest P&L", format="%.0f"),
                "difference": st.column_config.NumberColumn("Difference", format="%.0f"),
                "slippage": st.column_config.NumberColumn("Avg slippage / order (Rs per unit)", format="%.2f")})
            st.caption("Why the two differ: the paper broker fills at the live price when the rule fires (plus your "
                       "slippage setting), while the backtest fills exactly at the SL / target level. Backtest P&L "
                       "appears once that day's expired option data is downloaded (module 3).")
historical_note()
