"""Module 12 - Telegram Bot Control Panel.

Bot status and a test message, which Telegram messages are switched on, price alerts (add, delete, check now)
and the history of every message (sent / failed / skipped). The bot itself runs on its own:
python -m marketlens.alerts.bot (see the README: "Create your bot with @BotFather and find your chat ID").
"""
import pandas as pd
import streamlit as st

from marketlens import config
from marketlens.alerts import price_alerts as pa
from marketlens.alerts import telegram
from marketlens.ui import help_note, key_notice

st.title("💬 Telegram Bot Control Panel")
help_note("What this page does: Telegram bot status and a test message, which messages are sent, price alerts "
          "and the history of every message.\n"
          "How to read it: alert levels in Rs (price), % (change today), contracts (OI) or a ratio (PCR); "
          "statuses: sent, failed (with the reason) or skipped (keys not set / switched off).")
key_notice("Telegram", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
           "Messages are not sent; they are still saved below as 'skipped'.")

from marketlens.db import connection_status  # noqa: E402

DB_OK, DB_TEXT = connection_status()
if not DB_OK:
    st.error(f"The database is not connected, so alerts and the message history cannot be shown: {DB_TEXT}")
    st.stop()

status_tab, settings_tab, alerts_tab, history_tab = st.tabs(
    ["🤖 Bot status", "🔔 Notifications", "📈 Price alerts", "🧾 Message history"])

# ---------------------------------------------------------------- bot status and test message
with status_tab:
    from marketlens.trading import live_runner as lr
    from marketlens.trading import monitor as mon
    beats = lr.heartbeats()
    bot_state, bot_text = mon.heartbeat_state(beats.get("telegram_bot"))
    alert_state, alert_text = mon.heartbeat_state(beats.get(pa.HEARTBEAT))
    c = st.columns(3)
    with c[0].container(border=True):
        st.markdown("**Telegram keys**")
        st.markdown("🟢 Set" if telegram.is_configured() else "🔴 Not set (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
    with c[1].container(border=True):
        st.markdown("**Bot (commands and buttons)**")
        st.markdown(("🟢 " if bot_state == "alive" else "⚪ " if bot_state == "never" else "🔴 ")
                    + (bot_text if bot_state != "never" else "Not started"))
    with c[2].container(border=True):
        st.markdown("**Price alert watcher**")
        st.markdown(("🟢 " if alert_state == "alive" else "⚪ " if alert_state == "never" else "🔴 ")
                    + (alert_text if alert_state != "never" else "Not started"))
    if bot_state != "alive":
        st.info("Start the bot in a terminal (it keeps running): `python -m marketlens.alerts.bot`.  \n"
                "Check price alerts every minute in market hours: `python -m marketlens.alerts.price_alerts watch`.")
    st.markdown("Commands: " + ", ".join(f"`/{name}`" for name in ("status", "positions", "orders", "pnl",
                                                                    "strategies", "pause", "resume", "alerts",
                                                                    "addalert", "delalert", "stop", "resume_all",
                                                                    "squareoff", "report", "login")))
    if st.button("📨 Send test message", key="tg_test"):
        ok = telegram.notify("✅ Test message from MarketLens: your bot and chat ID work.", source="panel",
                             event="test")
        last = telegram.recent_notifications(1)[0]
        if ok:
            st.success("Sent. Check Telegram.")
        else:
            st.error(f"Not sent ({last['status']}): {last['error']}")

# ---------------------------------------------------------------- which notifications are on
with settings_tab:
    st.markdown("Choose which messages Telegram gets. Switched-off messages are still saved in the history "
                "(as *skipped*). **Always sent:** live orders waiting for your Confirm, the kill switch, price "
                "alerts and the morning login reminder.")
    current = telegram.notification_settings()
    with st.form("tg_settings"):
        chosen = {key: st.checkbox(label, value=current[key], key=f"tg_on_{key}")
                  for key, (label, _) in telegram.CATEGORIES.items()}
        if st.form_submit_button("💾 Save"):
            telegram.set_notification_settings(chosen)
            st.success("Saved.")

# ---------------------------------------------------------------- price alerts
with alerts_tab:
    from marketlens.signals.rules import OPERANDS, OPERATORS
    st.markdown("#### Add a price alert")
    f = st.columns([1.2, 1.6, 1, 1.6])
    symbol = f[0].text_input("Symbol", "NIFTY", key="pa_symbol", help="NIFTY, BANKNIFTY, RELIANCE, TCS ...")
    condition = f[1].selectbox("When", list(pa.CONDITIONS), format_func=pa.CONDITIONS.get, key="pa_condition")
    rule, side, target = None, "total", None
    if condition == "INDICATOR":
        g = st.columns(4)
        kind = g[0].selectbox("Indicator", [k for k in OPERANDS if k != "number"], index=list(OPERANDS).index("rsi"),
                              format_func=lambda k: OPERANDS[k][0], key="pa_kind")
        length = g[1].number_input("Length (days)", 1, 400, 14, key="pa_length")
        op = g[2].selectbox("Is", [o for o in OPERATORS], format_func=OPERATORS.get, index=list(OPERATORS).index("<"),
                            key="pa_op")
        value = g[3].number_input("Value (in the indicator's own unit)", value=30.0, key="pa_value")
        rule = {"conditions": [{"left": {"kind": kind, "length": int(length)}, "op": op,
                                "right": {"kind": "number", "value": float(value)}}], "join": "AND"}
        st.caption("Checked on the last finished daily candle (yfinance).")
    else:
        unit = {"ABOVE": "Rs", "BELOW": "Rs", "CHANGE_ABOVE": "% today", "CHANGE_BELOW": "% today",
                "OI_ABOVE": "contracts", "OI_BELOW": "contracts", "PCR_ABOVE": "ratio", "PCR_BELOW": "ratio"}[condition]
        target = f[2].number_input(f"Level ({unit})", value=0.0, step=1.0, key="pa_target")
        if condition.startswith("OI"):
            side = f[3].selectbox("Open interest of", list(pa.OI_SIDES), format_func=pa.OI_SIDES.get, key="pa_side")
        if condition in pa.CHAIN:
            st.caption(f"OI and PCR: {', '.join(pa.OPTION_INDICES)} only, nearest expiry; needs the Dhan keys "
                       "(never triggered on DEMO data).")
    note = st.text_input("Note (optional)", key="pa_note")
    if st.button("➕ Add alert", key="pa_add", type="primary"):
        try:
            new_id = pa.add_alert(symbol, condition, target, rule=rule, side=side, note=note)
            st.success(f"Alert #{new_id} added.")
        except ValueError as err:
            st.error(str(err))

    st.markdown("#### Your alerts")
    alerts = pa.list_alerts()
    if alerts.empty:
        st.caption("No alerts yet.")
    else:
        st.dataframe(alerts[["id", "description", "is_active", "last_value", "last_checked_at", "triggered_at",
                             "trigger_message", "note"]], hide_index=True, width="stretch",
                     column_config={"is_active": st.column_config.CheckboxColumn("Active"),
                                    "last_checked_at": st.column_config.DatetimeColumn("Last checked",
                                                                                       format="DD-MMM HH:mm"),
                                    "triggered_at": st.column_config.DatetimeColumn("Triggered", format="DD-MMM HH:mm")})
        d = st.columns([2, 1, 1])
        labels = dict(zip(alerts["id"], alerts["description"]))
        pick = d[0].selectbox("Alert", list(labels), format_func=lambda i: f"#{i} {labels[i]}", key="pa_pick")
        if d[1].button("🗑 Delete", key="pa_delete", width="stretch"):
            pa.delete_alert(int(pick))
            st.rerun()
    if st.button("🔎 Check alerts now", key="pa_check"):
        with st.spinner("Checking prices ..."):
            results = pa.check_alerts()
        if not results:
            st.info("No active alerts to check.")
        else:
            st.dataframe(pd.DataFrame(results)[["id", "description", "value", "status", "triggered", "message"]],
                         hide_index=True, width="stretch")
            hit = sum(r["triggered"] for r in results)
            (st.success if hit else st.info)(f"{hit} alert(s) triggered" + (" and sent to Telegram." if hit else "."))

# ---------------------------------------------------------------- message history
with history_tab:
    rows = pd.DataFrame(telegram.recent_notifications(500))
    if rows.empty:
        st.caption("No messages yet.")
    else:
        h = st.columns(3)
        statuses = h[0].multiselect("Status", ["sent", "failed", "skipped", "pending"], ["sent", "failed", "skipped"],
                                    key="tg_hist_status")
        sources = h[1].multiselect("From", sorted(rows["source"].dropna().unique()), key="tg_hist_source")
        text = h[2].text_input("Message contains", key="tg_hist_text")
        shown = rows[rows["status"].isin(statuses)]
        if sources:
            shown = shown[shown["source"].isin(sources)]
        if text.strip():
            shown = shown[shown["message"].str.contains(text.strip(), case=False, regex=False)]
        st.dataframe(shown[["created_at", "status", "source", "event", "message", "error"]], hide_index=True,
                     width="stretch", column_config={"created_at": st.column_config.DatetimeColumn(
                         "Time", format="DD-MMM HH:mm:ss")})
        counts = rows["status"].value_counts().to_dict()
        st.caption(f"Last {len(rows)} messages: " + ", ".join(f"{n} {s}" for s, n in counts.items())
                   + f". Telegram is {'set up' if telegram.is_configured() else 'not set up'} "
                   f"({'TRADING_MODE=' + config.TRADING_MODE}).")
