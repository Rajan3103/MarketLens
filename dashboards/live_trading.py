"""Module 11 - Live Algo Trading & Bot Monitor.

Deploy strategies from the Strategy Library, watch them run, and stop everything when needed.
The strategies run in a background process (python -m marketlens.trading.live_runner), so they keep
going when this page or the browser is closed. Every order goes through the order service (risk checks,
kill switch, Telegram Confirm in live mode). Paper or live follows TRADING_MODE in .env.
"""
import datetime as dt

import pandas as pd
import streamlit as st

from marketlens import config
from marketlens.broker.candles import IST
from marketlens.trading import forward as fw
from marketlens.trading import live_runner as lr
from marketlens.trading import monitor as mon
from marketlens.trading.order_service import OrderService, market_is_open
from marketlens.ui import historical_note

LIVE = config.TRADING_MODE == "live"
REFRESH = 10 if market_is_open(dt.datetime.now(IST)) else None      # auto refresh only in market hours
STATUS_ICONS = {"running": "🟢", "paused": "⏸️", "stopping": "🟠", "stopped": "⏹️"}

st.title("🤖 Live Algo Trading & Bot Monitor")


# ---------------------------------------------------------------- banner and status cards

# PAPER (blue) or LIVE (red), AUTO_TRADE and the kill switch, in one big banner.
def banner():
    try:
        killed, why = mon.kill_state()
    except Exception as err:
        killed, why = False, f"unknown ({err})"
    color, word = ("#c62828", "LIVE: REAL MONEY") if LIVE else ("#1565c0", "PAPER: VIRTUAL MONEY")
    auto = "AUTO_TRADE on: live orders go without asking" if config.AUTO_TRADE else \
        "AUTO_TRADE off: every live order waits for your Confirm"
    kill = f"🛑 KILL SWITCH ON ({why})" if killed else "Kill switch off"
    st.markdown(f"<div style='background:{color};color:white;padding:14px 18px;border-radius:8px;"
                f"font-size:1.35rem;font-weight:700'>{word}<div style='font-size:0.95rem;font-weight:400;"
                f"margin-top:4px'>{auto} &nbsp;·&nbsp; {kill}</div></div>", unsafe_allow_html=True)
    if killed:
        st.error("Kill switch is ON: no new orders today. Exits (SL / target / Stop) still go through. "
                 "Switch it off in the Loss limits tab.")


# One status card: a title, a coloured state word and a short line.
def card(column, title: str, ok: bool | None, text: str):
    icon = "🟢" if ok else ("⚪" if ok is None else "🔴")
    with column.container(border=True):
        st.markdown(f"**{title}**")
        st.markdown(f"{icon} {text}")


# Dhan login, runner, webhook server, Telegram bot, scheduler and database.
def status_cards():
    from marketlens.db import connection_status
    db_ok, db_text = connection_status()
    cols = st.columns(6)
    login = mon.login_today() if db_ok else None
    if login is None:
        card(cols[0], "Dhan login today", False, "Not checked today")
    else:
        expiry = f", until {login['expiry'].astimezone(IST):%d-%b %H:%M}" if login["expiry"] else ""
        card(cols[0], "Dhan login today", login["status"] == "active", f"{login['status']}{expiry}")
    beats = lr.heartbeats() if db_ok else {}
    state, text = mon.heartbeat_state(beats.get(lr.RUNNER))
    card(cols[1], "Live runner", state == "alive" if state != "never" else None, text)
    hook_ok, hook_text = mon.webhook_status()
    card(cols[2], "Webhook server", hook_ok, hook_text)
    bot_state, bot_text = mon.heartbeat_state(beats.get("telegram_bot"))
    card(cols[3], "Telegram bot", bot_state == "alive" if bot_state != "never" else None,
         bot_text if bot_state != "never" else "Not built yet (module 12)")
    sch_state, sch_text = mon.heartbeat_state(beats.get("scheduler"))
    card(cols[4], "Scheduler", sch_state == "alive" if sch_state != "never" else None,
         sch_text if sch_state != "never" else "Not built yet")
    card(cols[5], "Database", db_ok, "Connected" if db_ok else "Not connected")
    if not db_ok:
        st.error(db_text)
        st.stop()
    if login is None or login["status"] != "active":
        with st.expander("🔑 Dhan login not valid today: how to renew (needs you, because of 2FA)",
                         expanded=LIVE):
            from marketlens.broker.session import DAILY_STEPS
            st.code(DAILY_STEPS, language=None)
            if st.button("Check the login now", key="lt_check_login"):
                from marketlens.broker.session import check_login
                result = check_login()
                (st.success if result.status == "active" else st.error)(f"{result.status}: {result.message}")


@st.fragment(run_every=REFRESH)
def header():
    banner()
    status_cards()
    st.caption(f"Updated {dt.datetime.now(IST):%H:%M:%S}" + (" · refreshes every 10 s in market hours"
                                                              if REFRESH else " · market closed, no auto refresh"))


header()

alive, age = lr.runner_alive()
if not alive:
    r1, r2 = st.columns([4, 1])
    r1.warning("The live runner is not running, so deployed strategies do not trade. Start it here, or in a "
               "terminal: `python -m marketlens.trading.live_runner` (add `--once` for a single cycle).")
    if r2.button("▶️ Start the live runner", key="lt_start_runner", width="stretch"):
        pid = lr.start_process()
        st.success(f"Started in the background (process {pid}). Log: logs/live_runner.log")

deploy_tab, monitor_tab, limits_tab, errors_tab = st.tabs(
    ["🚀 Deploy", "📡 Monitor", "🛑 Loss limits & kill switch", "🧾 Error log"])

# ---------------------------------------------------------------- deploy
with deploy_tab:
    try:
        strategies = fw.list_strategies()
    except Exception as err:
        strategies = pd.DataFrame(columns=["id", "name", "kind", "underlying"])
        st.error(f"Could not read the Strategy Library: {err}")
    handoff = st.session_state.get("handoff")
    if handoff and handoff.get("to") == "live":
        st.session_state["lt_pick"] = handoff["strategy_id"]
        st.session_state.pop("handoff")
    if strategies.empty:
        st.info("No runnable strategies yet. Save one from the Options Backtester (page 4) or make an indicator "
                "strategy on page 6, forward-test it, then deploy it here.")
    else:
        ids = list(strategies["id"])
        labels = {row.id: f"{row.name} ({row.kind}, {row.underlying})" for row in strategies.itertuples()}
        pick = st.session_state.get("lt_pick")
        sid = st.selectbox("Strategy from the library", ids, index=ids.index(pick) if pick in ids else 0,
                           format_func=labels.get, key="lt_strategy")
        check = lr.deploy_check(sid)
        c = st.columns(2)
        c[0].markdown(f"{'✅' if check['has_backtest'] else '❌'} Saved backtest")
        c[1].markdown(f"{'✅' if check['forward_days'] >= lr.MIN_FORWARD_DAYS else '❌'} Forward-test days: "
                      f"**{check['forward_days']} / {lr.MIN_FORWARD_DAYS}**")
        if check["missing"]:
            (st.error if LIVE else st.info)(("Live deployment blocked. " if LIVE else
                                             "Paper deployment is allowed now; before LIVE it still needs: ")
                                            + "; ".join(check["missing"]) + ".")
        f = st.columns(4)
        multiplier = f[0].number_input("Lots multiplier", 1, 50, 1, key="lt_mult",
                                       help="Every leg's lots (or the indicator quantity) times this number.")
        max_loss = f[1].number_input("Max loss for this strategy today (Rs, 0 = none)", 0.0, 1e8,
                                     float(config.MAX_STRATEGY_LOSS_RS), step=500.0, key="lt_max_loss")
        mode = f[2].radio("Orders", ["Confirm each on Telegram", "Automatic"], key="lt_confirm",
                          help="Live only. Automatic also needs AUTO_TRADE=true in .env; paper always sends at once.")
        capital = f[3].number_input("Paper capital (Rs)", 10_000.0, 1e9, float(config.PAPER_CAPITAL), step=50_000.0,
                                    key="lt_capital", disabled=LIVE)
        if mode == "Automatic" and LIVE and not config.AUTO_TRADE:
            st.caption("AUTO_TRADE=false in .env, so orders still wait for your Confirm.")
        if mode == "Confirm each on Telegram" and LIVE:
            st.caption("In confirm mode stop-loss and target exits also wait for your Confirm "
                       f"(cancelled after {config.CONFIRM_TIMEOUT_MIN} minutes).")
        if st.button(f"🚀 Deploy in {config.TRADING_MODE.upper()} mode", type="primary", key="lt_deploy",
                     disabled=LIVE and not check["ok_live"]):
            try:
                st.success(lr.deploy(sid, int(multiplier), float(max_loss), mode != "Automatic",
                                     None if LIVE else float(capital)))
            except Exception as err:
                st.error(str(err))

    st.markdown("#### Deployed strategies")
    deployments = lr.list_deployments()
    if deployments.empty:
        st.caption("None yet.")
    for d in deployments.itertuples():
        row = st.columns([3, 1.4, 1.2, 0.8, 0.8, 0.8])
        row[0].markdown(f"{STATUS_ICONS.get(d.status, '')} **{d.name}** · {d.mode.upper()} · lots x{d.lots_multiplier}"
                        f" · {'confirm' if d.confirm else 'auto'}" + (f"  \n{d.message}" if d.message else ""))
        row[1].markdown(f"Status: **{d.status}**  \nState: {d.state or '-'}")
        row[2].metric("Today", f"Rs {d.pnl:,.0f}", label_visibility="collapsed")
        if row[3].button("▶️ Start", key=f"lt_run_{d.strategy_id}", disabled=d.status == "running", width="stretch"):
            try:
                lr.set_status(d.strategy_id, "running")
                st.rerun()
            except Exception as err:
                st.error(str(err))
        if row[4].button("⏸ Pause", key=f"lt_pause_{d.strategy_id}", disabled=d.status != "running", width="stretch"):
            lr.set_status(d.strategy_id, "paused")
            st.rerun()
        if row[5].button("⏹ Stop", key=f"lt_stop_{d.strategy_id}", disabled=d.status in ("stopping", "stopped"),
                         width="stretch"):
            lr.set_status(d.strategy_id, "stopping")
            st.rerun()
    st.caption("Pause: no new orders, open positions stay. Stop: this strategy's positions are closed at the "
               "runner's next minute, then it stops.")

# ---------------------------------------------------------------- monitor
with monitor_tab:
    @st.fragment(run_every=REFRESH)
    def monitor():
        service = OrderService()
        st.markdown("#### Orders waiting for your confirmation")
        waiting = mon.waiting_signals()
        if waiting.empty:
            st.caption("None.")
        for w in waiting.itertuples():
            row = st.columns([4, 1, 1])
            row[0].markdown(f"**Signal {w.signal_id}** ({w.strategy}, {w.placed_at}, waiting {w.minutes_waiting} min): "
                            f"{w.legs}" + (f" · {w.reason}" if w.reason else ""))
            if row[1].button("✅ Confirm", key=f"lt_ok_{w.signal_id}", width="stretch"):
                result = service.confirm_signal(int(w.signal_id))
                (st.success if result.status == "SENT" else st.warning)(f"{result.status}: {result.message}")
            if row[2].button("❌ Reject", key=f"lt_no_{w.signal_id}", width="stretch"):
                result = service.reject_signal(int(w.signal_id))
                st.info(f"{result.status}: {result.message}")

        st.markdown("#### Running strategies (today's P&L)")
        table = lr.list_deployments()
        table = table[table["status"].isin(lr.ACTIVE)]
        if table.empty:
            st.caption("No strategy is deployed and running.")
        else:
            st.dataframe(table[["name", "mode", "status", "state", "pnl", "lots_multiplier", "message"]],
                         hide_index=True, width="stretch",
                         column_config={"pnl": st.column_config.NumberColumn("Today's P&L", format="%.0f")})

        st.markdown("#### Open positions")
        positions, note = mon.open_positions()
        st.caption(note)
        if positions.empty:
            st.caption("None.")
        else:
            st.dataframe(positions, hide_index=True, width="stretch")

        st.markdown("#### Today's orders")
        orders = mon.orders_today()
        if orders.empty:
            st.caption("No orders today.")
        else:
            st.dataframe(orders, hide_index=True, width="stretch")

    monitor()

# ---------------------------------------------------------------- loss limits and kill switch
with limits_tab:
    @st.fragment(run_every=REFRESH)
    def limits():
        daily, per = mon.loss_limits(live_total=mon.live_day_total() if LIVE else None)
        st.markdown("#### Daily loss limit (all strategies)")
        st.progress(daily["used"], text=f"Today's P&L Rs {daily['pnl']:,.0f} · limit Rs {daily['limit']:,.0f} "
                                        f"(MAX_DAILY_LOSS_RS) · {daily['used']:.0%} used")
        st.markdown("#### Per-strategy loss limits")
        if per.empty:
            st.caption("No strategy has traded today.")
        for p in per.itertuples():
            limit = f"Rs {p.limit:,.0f}" if p.limit else "no limit"
            st.progress(p.used, text=f"{p.strategy}: Rs {p.pnl:,.0f} · limit {limit} · {p.used:.0%} used")

    limits()
    st.divider()
    killed, why = mon.kill_state()
    k1, k2 = st.columns(2)
    with k1.container(border=True):
        st.markdown("**🛑 Kill switch: stop all trading**")
        st.caption("Blocks every new order for the rest of the day. Exits of open positions (SL, target, Stop) "
                   "still go through.")
        sure_kill = st.checkbox("Yes, block all new orders today", key="lt_sure_kill")
        if st.button("Kill switch ON", type="primary", disabled=not sure_kill or killed, key="lt_kill"):
            OrderService().kill("switched on from page 11")
            st.rerun()
        if killed and st.button("Switch the kill switch OFF", key="lt_unkill"):
            OrderService().unkill("switched off from page 11")
            st.rerun()
    with k2.container(border=True):
        st.markdown("**⛔ Square off everything**")
        st.caption(("Sends Dhan's exit-all (every position, pending orders cancelled)" if LIVE else
                    "Closes every paper position at its last price") +
                   ", switches the kill switch on and stops every deployed strategy.")
        sure_all = st.checkbox("Yes, close every position now", key="lt_sure_all")
        if st.button("Square off everything", type="primary", disabled=not sure_all, key="lt_square"):
            lr.set_all("stopped", "Stopped: square off everything from page 11.")
            result = OrderService().kill("square off everything from page 11", square_off=True)
            st.warning(f"Done: {result}")

# ---------------------------------------------------------------- error log
with errors_tab:
    e = st.columns([1, 1.5, 1.5, 2])
    days = e[0].selectbox("Period", [1, 7, 30], format_func=lambda d: "today" if d == 1 else f"last {d} days",
                          key="lt_err_days")
    levels = e[1].multiselect("Level", ["ERROR", "WARNING"], ["ERROR", "WARNING"], key="lt_err_levels")
    module = e[2].selectbox("Module", ["(all)"] + mon.error_modules(), key="lt_err_module")
    text = e[3].text_input("Message contains", key="lt_err_text")
    errors = mon.error_log(days, levels, None if module == "(all)" else module, text)
    if errors.empty:
        st.success("No errors for these filters.")
    else:
        st.dataframe(errors, hide_index=True, width="stretch")
        st.caption("The full log, with every step, is in logs/marketlens.log (and logs/live_runner.log).")

historical_note()
