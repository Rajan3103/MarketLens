"""Module 7 - Signals & Indicator Strategy Builder.

Build entry / exit rules from indicators (no code), backtest them, scan a watchlist for where
they are true today, export them as TradingView Pine Script v6 with MarketLens webhook alerts,
and see the webhook address and the latest alerts received. The same rules drive every tab.
"""
import datetime as dt
import json

import pandas as pd
import streamlit as st

from marketlens import config
from marketlens.backtest.ui import show_backtest
from marketlens.signals import backtest as sb
from marketlens.signals import pine
from marketlens.signals import rules as sr
from marketlens.ui import cost_settings_inputs, historical_note
from marketlens.webhook import payload as wh

FIELD_LABELS = {"length": "Length", "mult": "Mult", "fast": "Fast", "slow": "Slow", "signal": "Signal", "value": "Value"}
DEFAULT_ENTRY = [{"left": {"kind": "ema", "length": 9}, "op": "crosses_above", "right": {"kind": "ema", "length": 21}}]
DEFAULT_EXIT = [{"left": {"kind": "ema", "length": 9}, "op": "crosses_below", "right": {"kind": "ema", "length": 21}}]


# ---------------------------------------------------------------- condition rows in session state

# Give a condition row a new id (so its input boxes get their own keys).
def _new_row(values: dict) -> dict:
    st.session_state["sb_uid"] = st.session_state.get("sb_uid", 0) + 1
    return {**values, "uid": st.session_state["sb_uid"]}


# Put a strategy's rules and settings into the page (used on first open and when loading a saved one).
def load_into_page(strategy: sr.SignalStrategy | None = None):
    if strategy is None:
        entry, exit_ = DEFAULT_ENTRY, DEFAULT_EXIT
        form = {"symbol": "RELIANCE", "timeframe": "1 day", "start": dt.date.today() - dt.timedelta(days=3 * 365),
                "end": dt.date.today(), "side": "long", "entry_join": "AND", "exit_join": "OR", "sl_pct": 0.0,
                "target_pct": 0.0, "qty": 10, "capital": 500_000.0}
    else:
        entry = [{"left": c.left.__dict__, "op": c.op, "right": c.right.__dict__} for c in strategy.entry]
        exit_ = [{"left": c.left.__dict__, "op": c.op, "right": c.right.__dict__} for c in strategy.exit]
        form = {k: getattr(strategy, k) for k in ("symbol", "timeframe", "start", "end", "side", "entry_join",
                                                  "exit_join", "qty", "capital")}
        form["sl_pct"], form["target_pct"] = strategy.sl_pct or 0.0, strategy.target_pct or 0.0
    st.session_state["sb_entry"] = [_new_row(r) for r in entry]
    st.session_state["sb_exit"] = [_new_row(r) for r in exit_]
    st.session_state["sb_form"] = form
    st.session_state["sb_ver"] = st.session_state.get("sb_ver", 0) + 1
    st.session_state.pop("sb_result", None)


# Add / remove a condition row.
def add_row(which: str):
    st.session_state[which].append(_new_row({"left": {"kind": "close"}, "op": ">", "right": {"kind": "sma", "length": 50}}))


def remove_row(which: str, uid: int):
    st.session_state[which] = [r for r in st.session_state[which] if r["uid"] != uid]


# Load a strategy saved earlier.
def load_saved():
    row = st.session_state.get("sb_saved_pick")
    if row is not None:
        saved = sr.list_signal_strategies()
        params = saved.loc[saved["id"] == row, "params"].iloc[0]
        load_into_page(sr.SignalStrategy.from_params(params))


# Input boxes for one operand; returns its values.
def operand_inputs(cols, key: str, default: dict) -> dict:
    kinds = list(sr.OPERANDS)
    kind = cols[0].selectbox("Indicator", kinds, index=kinds.index(default.get("kind", "close")),
                             format_func=lambda k: sr.OPERANDS[k][0], key=f"{key}_kind", label_visibility="collapsed")
    base = sr.Operand()
    values = {"kind": kind}
    for n, field in enumerate(sr.OPERANDS[kind][1]):
        start = default.get(field, getattr(base, field)) if default.get("kind") == kind else getattr(base, field)
        if field in ("mult", "value"):
            values[field] = cols[n + 1].number_input(FIELD_LABELS[field], value=float(start), step=0.5,
                                                     key=f"{key}_{kind}_{field}")
        else:
            values[field] = cols[n + 1].number_input(FIELD_LABELS[field], min_value=1, value=int(start), step=1,
                                                     key=f"{key}_{kind}_{field}")
    return values


# Draw one list of conditions (entry or exit) and return them as plain dicts.
def conditions_editor(which: str, title: str, join_key: str, join_default: str) -> tuple[list[dict], str]:
    st.markdown(f"**{title}**")
    join = st.radio("Join with", sr.JOINS, index=sr.JOINS.index(join_default), horizontal=True,
                    key=f"{join_key}_{st.session_state['sb_ver']}",
                    format_func=lambda j: "AND (all must be true)" if j == "AND" else "OR (any one is enough)")
    out = []
    for row in st.session_state[which]:
        uid = row["uid"]
        with st.container(border=True):
            cols = st.columns([1.4, 0.7, 0.7, 0.7, 1.2, 1.4, 0.7, 0.7, 0.7, 0.35])
            left = operand_inputs(cols[0:4], f"sb_{uid}_l", row["left"])
            ops = list(sr.OPERATORS)
            op = cols[4].selectbox("Operator", ops, index=ops.index(row["op"]), format_func=sr.OPERATORS.get,
                                   key=f"sb_{uid}_op", label_visibility="collapsed")
            right = operand_inputs(cols[5:9], f"sb_{uid}_r", row["right"])
            cols[9].button("✕", key=f"sb_{uid}_del", on_click=remove_row, args=(which, uid), help="Remove")
        out.append({"left": left, "op": op, "right": right})
    st.button("➕ Add condition", key=f"sb_add_{which}", on_click=add_row, args=(which,))
    return out, join


# ---------------------------------------------------------------- page

st.title("📐 Signals & Indicator Strategy Builder")
st.caption("Rules from indicators, without code: backtest them, scan for them, export them to TradingView.")
if "sb_form" not in st.session_state:
    load_into_page()
form, v = st.session_state["sb_form"], st.session_state["sb_ver"]

build_tab, scan_tab, pine_tab, hook_tab = st.tabs(["🧪 Build & backtest", "🔎 Scanner", "🌲 Pine Script export",
                                                   "🔔 Webhook alerts"])

with build_tab:
    try:
        saved = sr.list_signal_strategies()
    except Exception:
        saved = pd.DataFrame(columns=["id", "name", "params"])
    if len(saved):
        l1, l2 = st.columns([3, 1])
        l1.selectbox("Load a saved strategy", list(saved["id"]), key="sb_saved_pick",
                     format_func=lambda i: saved.loc[saved["id"] == i, "name"].iloc[0])
        l2.button("Load", on_click=load_saved, key="sb_load", width="stretch")

    c = st.columns([1.2, 1, 1, 1, 1])
    symbol = c[0].text_input("Instrument (NSE symbol)", form["symbol"], key=f"sb_symbol_{v}",
                             help="e.g. RELIANCE, NIFTYBEES, NIFTY, BANKNIFTY")
    timeframe = c[1].selectbox("Timeframe", list(sr.TIMEFRAMES), index=list(sr.TIMEFRAMES).index(form["timeframe"]),
                               key=f"sb_tf_{v}")
    start = c[2].date_input("From", form["start"], key=f"sb_start_{v}", format="DD/MM/YYYY")
    end = c[3].date_input("To", form["end"], key=f"sb_end_{v}", format="DD/MM/YYYY")
    side = c[4].radio("Direction", ["long", "short"], index=["long", "short"].index(form["side"]), horizontal=True,
                      key=f"sb_side_{v}", format_func=lambda x: "Buy (long)" if x == "long" else "Sell (short)")

    entry_rows, entry_join = conditions_editor("sb_entry", "Entry: when to enter (checked at each candle close)",
                                               "sb_ejoin", form["entry_join"])
    exit_rows, exit_join = conditions_editor("sb_exit", "Exit: when to leave (optional; SL / target also exit)",
                                             "sb_xjoin", form["exit_join"])
    k = st.columns(4)
    sl_pct = k[0].number_input("Stop loss % (0 = off)", 0.0, 100.0, float(form["sl_pct"]), step=0.5, key=f"sb_sl_{v}")
    target_pct = k[1].number_input("Target % (0 = off)", 0.0, 1000.0, float(form["target_pct"]), step=0.5,
                                   key=f"sb_tg_{v}")
    qty = k[2].number_input("Quantity (shares)", 1, 1_000_000, int(form["qty"]), key=f"sb_qty_{v}")
    capital = k[3].number_input("Capital (Rs)", 10_000.0, 1e9, float(form["capital"]), step=50_000.0, key=f"sb_cap_{v}")
    costs = cost_settings_inputs("signals")
    st.caption("Daily timeframes hold positions overnight (delivery costs); intraday timeframes close at the end "
               "of each day (intraday costs). A rule true at a candle's close fills at the next candle's open.")

    strategy, problem = None, None
    try:
        strategy = sr.SignalStrategy(symbol, timeframe, start, end, side, entry_rows, entry_join, exit_rows, exit_join,
                                     sl_pct or None, target_pct or None, qty, capital, costs)
    except (ValueError, TypeError) as err:
        problem = str(err)
    if problem:
        st.error(f"Fix this first: {problem}")
    elif st.button("▶️ Run backtest", type="primary", key="sb_run"):
        with st.spinner("Loading candles and testing the rules..."):
            try:
                candles = sb.load_candles(strategy.symbol, strategy.timeframe, strategy.start, strategy.end)
                result, markers = sb.run_signal_backtest(strategy, candles)
                st.session_state["sb_result"] = (strategy, candles, result, markers)
            except Exception as err:
                st.error(f"The backtest stopped: {err}")

    ran = st.session_state.get("sb_result")
    if ran:
        r_strategy, candles, result, markers = ran
        st.caption(f"Data: {result.extras.get('source', '?')} · {result.extras.get('candles', 0):,} candles · "
                   f"rules: {r_strategy.name}")
        st.plotly_chart(sb.signal_chart(r_strategy, candles, markers), width="stretch", key="sb_chart")
        show_backtest(result, key="sb")

    st.subheader("Save to Strategy Library")
    s1, s2 = st.columns([3, 1])
    save_name = s1.text_input("Name", strategy.name[:80] if strategy else "", key="sb_save_name")
    auto = s1.checkbox("Auto: webhook alerts naming this strategy place paper orders by themselves", False,
                       key="sb_auto", help="Use the same name in the alert's \"strategy\" field. Paper (virtual "
                                           "money) unless TRADING_MODE=live and AUTO_TRADE=true.")
    if s2.button("💾 Save to Strategy Library", key="sb_save", disabled=strategy is None, width="stretch"):
        try:
            sid = sr.save_signal_strategy(save_name, strategy, auto=auto)
            st.success(f"Saved '{save_name}' (id {sid}){' with auto paper orders' if auto else ''}. It appears in "
                       "the Scanner tab and module 10.")
        except Exception as err:
            st.error(f"Could not save: {err}")

with scan_tab:
    st.caption("Lists where the ENTRY rule is true on the latest daily candle. **Historical signal, not a "
               "recommendation.** Uses stored daily prices (scripts/sync_prices.py), else yfinance.")
    a = st.columns([2, 1.5])
    choices = {"current": "The rules on the Build tab"}
    try:
        saved_list = sr.list_signal_strategies()
        choices.update({int(i): f"Saved: {n}" for i, n in zip(saved_list["id"], saved_list["name"])})
    except Exception:
        saved_list = pd.DataFrame(columns=["id", "name", "params"])
    pick = a[0].selectbox("Rules", list(choices), format_func=choices.get, key="sc_rules")
    universe = a[1].selectbox("Watchlist", ["nifty50", "nifty200", "etfs", "my list"], key="sc_universe",
                              format_func=lambda u: {"nifty50": "Nifty 50", "nifty200": "Nifty 200", "etfs": "ETFs",
                                                     "my list": "My list"}[u])
    custom = st.text_area("My list (comma or new line separated)", "RELIANCE, TCS, INFY, HDFCBANK",
                          key="sc_custom") if universe == "my list" else ""
    rules_for_scan = strategy if pick == "current" else sr.SignalStrategy.from_params(
        saved_list.loc[saved_list["id"] == pick, "params"].iloc[0])
    if st.button("🔎 Scan", type="primary", key="sc_run", disabled=rules_for_scan is None):
        from marketlens.ingest.prices import load_universe
        symbols = ([s.strip().upper() for s in custom.replace("\n", ",").split(",") if s.strip()]
                   if universe == "my list" else list(load_universe(universe)["symbol"]))
        with st.spinner(f"Scanning {len(symbols)} symbols..."):
            try:
                st.session_state["sc_table"] = sb.scan(rules_for_scan, symbols)
            except Exception as err:
                st.error(f"The scan stopped: {err}")
    table = st.session_state.get("sc_table")
    if table is not None:
        hits = table[table["signal"] == True]  # noqa: E712  (the column can also hold None)
        st.success(f"Entry rule true today for {len(hits)} of {len(table)} symbols (historical signal, not a "
                   "recommendation).")
        only = st.checkbox("Show only where it is true", True, key="sc_only")
        st.dataframe(hits if only else table, hide_index=True, width="stretch")

with pine_tab:
    if strategy is None:
        st.info("Fix the rules on the Build tab first.")
    else:
        code = pine.rules_to_pine(strategy)
        problems = pine.validate_pine(code)
        if problems:
            st.error("The generated script failed a check:\n\n" + "\n".join(f"- {p}" for p in problems))
        else:
            st.success("Checked: //@version=6, strategy() with Indian costs, inputs for every setting, balanced "
                       "brackets, no Python syntax.")
        st.code(code, language="javascript")
        name = "".join(ch if ch.isalnum() else "_" for ch in strategy.symbol.lower()) + "_marketlens.pine"
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Download .pine", code, file_name=name, mime="text/plain", key="pine_download")
        d2.caption("📋 Copy: use the copy icon at the top right of the code box.")
        st.markdown("**Use it in TradingView, in 5 steps**")
        st.markdown(
            "1. Open a chart of the same symbol and timeframe, then click **Pine Editor** at the bottom.\n"
            "2. Delete what is there, paste this code, and click **Add to chart** (Save it with a name).\n"
            "3. Check the **Strategy Tester** tab: trades should look like the MarketLens backtest.\n"
            "4. Open the strategy's **Settings > Inputs** and replace `PASTE_YOUR_WEBHOOK_SECRET` with your "
            "`WEBHOOK_SECRET` from `.env` (never put it in the code itself).\n"
            "5. Click **Alert** (⏰): Condition = this strategy, choose **alert() function calls only**, "
            "tick **Webhook URL** and paste the address from the Webhook alerts tab. Keep the message as is.")
        with st.expander("Quick template: moving-average crossover"):
            m1, m2 = st.columns(2)
            fast = m1.number_input("Fast moving average", 2, 400, 20, key="pine_fast")
            slow = m2.number_input("Slow moving average", 3, 800, 50, key="pine_slow")
            template = pine.ma_crossover_script(fast, slow)
            st.code(template, language="javascript")
            st.download_button("⬇️ Download .pine", template, file_name=f"ma_crossover_{fast}_{slow}.pine",
                               key="pine_template_download")

with hook_tab:
    urls = wh.webhook_urls()
    st.markdown("**Your webhook addresses**")
    st.code(urls["tradingview"], language=None)
    st.caption("TradingView. " + ("This is your AWS server address." if config.is_set(config.SERVER_STATIC_IP) else
                                  "This computer for now; it becomes your AWS address once SERVER_STATIC_IP is set. "
                                  "TradingView can only reach a public address."))
    st.code(urls["chartink"], language=None)
    st.caption("Chartink: replace the placeholder after secret= with your WEBHOOK_SECRET.")
    if wh.secret_ready():
        st.success("🔑 WEBHOOK_SECRET is set (it is never shown here).")
    elif config.is_set(config.WEBHOOK_SECRET):
        st.warning(f"🔑 WEBHOOK_SECRET is set but short: use at least {config.WEBHOOK_SECRET_MIN_LENGTH} characters.")
    else:
        st.warning("🔑 WEBHOOK_SECRET is not set: alerts will be rejected. Add a long random text to .env.")
    st.markdown("**TradingView alert message** (the Pine export sends this by itself; paste it for other alerts)")
    message = wh.tradingview_message(strategy.name[:40] if strategy else "My strategy",
                                     action="BUY" if (strategy is None or strategy.side == "long") else "SELL")
    st.code(json.dumps(message, indent=2), language="json")
    st.markdown("**What Chartink sends** (you do not type it; just set the webhook address above in the scan's alert)")
    st.code(json.dumps(wh.CHARTINK_EXAMPLE, indent=2), language="json")
    st.markdown("**Last 50 webhook events**")
    try:
        events = wh.recent_events(50)
        if events.empty:
            st.caption("None received yet. Start the receiver in a terminal: `python -m marketlens.webhook.server`.")
        else:
            st.dataframe(events, hide_index=True, width="stretch")
    except Exception as err:
        st.caption(f"Could not read webhook events: {err}")

historical_note()
