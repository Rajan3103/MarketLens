"""Module 10 - Personal Analytics & Strategy Library.

Four tabs: every saved strategy (open it anywhere, duplicate, rename, delete, export / import),
holdings and positions with P&L and sector allocation, a journal of every trade with notes, tags,
rating and screenshot, and your combined realised performance against Nifty 50.
"""
import datetime as dt
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketlens import config
from marketlens import library as lib
from marketlens.backtest import charts
from marketlens.broker.candles import IST
from marketlens.broker.dhan_client import has_keys
from marketlens.ui import historical_note, key_notice, trading_mode_banner

SOURCE_NAMES = {"paper": "Paper", "forward": "Forward test", "live": "Live", "simulator": "Simulator"}


# Rupees with commas, or "-".
def rs(value) -> str:
    return "-" if value is None or pd.isna(value) else f"Rs {value:,.0f}"


# Hand a strategy to another page and go there (or say where to go when that is not possible here).
def open_elsewhere(strategy: dict, action: str):
    try:
        key, value, page = lib.handoff_for(strategy, action)
    except ValueError as err:
        st.error(str(err))
        return
    st.session_state[key] = value
    try:
        st.switch_page(page)
    except Exception:                    # e.g. the page is opened on its own, without the app menu
        st.info(f"Sent. Open **{Path(page).stem.replace('_', ' ')}** from the menu.")


st.title("📚 Personal Analytics & Strategy Library")
trading_mode_banner()
library_tab, holdings_tab, journal_tab, perf_tab = st.tabs(
    ["📚 Strategy library", "💼 Holdings & P&L", "📓 Trade journal", "📈 Performance"])

# ================================================================ 1. strategy library
with library_tab:
    msg = st.session_state.pop("lib_msg", None)
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])
    try:
        table = lib.list_library()
    except Exception as err:
        table = pd.DataFrame(columns=["id", "name"])
        st.error(f"Could not read the Strategy Library: {err}")
    if table.empty:
        st.info("No saved strategies yet. Save one from the Options Backtester (4), Signals builder (7) or "
                "ETF Momentum (9), or import a JSON file below.")
    else:
        st.dataframe(table.drop(columns=["source", "description"]), hide_index=True, width="stretch", column_config={
            "created": st.column_config.DatetimeColumn("Created", format="DD-MMM-YYYY"),
            "updated": st.column_config.DatetimeColumn("Updated", format="DD-MMM-YYYY"),
            "last_backtest": st.column_config.DatetimeColumn("Last backtest", format="DD-MMM-YYYY"),
            "bt_total_pnl": st.column_config.NumberColumn("Backtest P&L", format="%.0f"),
            "bt_cagr_pct": st.column_config.NumberColumn("CAGR %", format="%.2f"),
            "bt_max_dd_pct": st.column_config.NumberColumn("Max DD %", format="%.2f"),
            "bt_sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"),
            "bt_win_rate_pct": st.column_config.NumberColumn("Win %", format="%.1f"),
            "fwd_total_pnl": st.column_config.NumberColumn("Forward P&L", format="%.0f"),
            "fwd_last_day": st.column_config.DateColumn("Forward last day", format="DD-MMM-YYYY")})
        st.caption("Backtest columns = the latest backtest saved for that strategy; forward columns = all its "
                   "forward-test days (module 6).")
        names = dict(zip(table["id"], table["name"]))
        pick = st.selectbox("Strategy", list(names), format_func=lambda i: f"{names[i]} (id {i})", key="lib_pick")
        strategy = lib.get_strategy(pick)
        can = lib.actions_for(lib.source_of(strategy["params"]))
        b = st.columns(4)
        if b[0].button("🧪 Open in backtester", key="lib_bt", disabled=not can["backtest"], width="stretch",
                       help=can["why"].get("backtest")):
            open_elsewhere(strategy, "backtest")
        if b[1].button("⏪ Open in simulator", key="lib_sim", disabled=not can["simulator"], width="stretch",
                       help=can["why"].get("simulator")):
            open_elsewhere(strategy, "simulator")
        if b[2].button("📝 Start forward test", key="lib_fwd", disabled=not can["forward"], width="stretch",
                       help=can["why"].get("forward")):
            open_elsewhere(strategy, "forward")
        if b[3].button("🤖 Deploy live", key="lib_live", width="stretch",
                       help="Goes to module 11. Real orders only when TRADING_MODE=live."):
            open_elsewhere(strategy, "live")
        for reason in can["why"].values():
            st.caption(f"ℹ️ {reason}")

        with st.expander("Details (settings and legs)"):
            st.write(strategy["description"] or "")
            if strategy["legs"]:
                st.dataframe(pd.DataFrame(strategy["legs"]).drop(columns=["params"]), hide_index=True)
            st.json(strategy["params"], expanded=False)

        m = st.columns(3)
        with m[0].container(border=True):
            new = st.text_input("Duplicate as", f"{strategy['name']} (copy)", key="lib_dup_name")
            if st.button("⧉ Duplicate", key="lib_dup"):
                try:
                    new_id = lib.duplicate_strategy(pick, new)
                    st.session_state["lib_msg"] = ("ok", f"Copied to '{lib.get_strategy(new_id)['name']}' (id {new_id}).")
                    st.rerun()
                except ValueError as err:
                    st.error(str(err))
        with m[1].container(border=True):
            new = st.text_input("Rename to", strategy["name"], key="lib_ren_name")
            if st.button("✏️ Rename", key="lib_ren"):
                try:
                    st.session_state["lib_msg"] = ("ok", f"Renamed to '{lib.rename_strategy(pick, new)}'.")
                    st.rerun()
                except ValueError as err:
                    st.error(str(err))
        with m[2].container(border=True):
            sure = st.checkbox(f"Yes, delete '{strategy['name']}'", key="lib_del_sure",
                               help="Its legs and forward-test days are deleted; backtests, orders and journal "
                                    "notes stay (without the link).")
            if st.button("🗑️ Delete", key="lib_del", disabled=not sure):
                try:
                    st.session_state["lib_msg"] = ("ok", f"Deleted '{lib.delete_strategy(pick)}'.")
                    st.session_state.pop("lib_pick", None)
                    st.rerun()
                except ValueError as err:
                    st.error(str(err))

    e1, e2 = st.columns(2)
    with e1.container(border=True):
        st.markdown("**Export as JSON**")
        if not table.empty:
            export_names = dict(zip(table["id"], table["name"]))
            chosen = st.multiselect("Strategies", list(export_names), default=[st.session_state.get("lib_pick")]
                                    if st.session_state.get("lib_pick") in export_names else [],
                                    format_func=export_names.get, key="lib_export")
            if chosen:
                st.download_button("⬇️ Download JSON", lib.export_strategies(chosen),
                                   f"marketlens_strategies_{dt.date.today():%Y%m%d}.json", "application/json",
                                   key="lib_export_btn")
    with e2.container(border=True):
        st.markdown("**Import from JSON**")
        upload = st.file_uploader("A file exported from MarketLens", type=["json"], key="lib_import")
        if upload is not None and st.button("⬆️ Import", key="lib_import_btn"):
            try:
                added = lib.import_strategies(upload.getvalue())
                st.session_state["lib_msg"] = ("ok", "Imported: " + ", ".join(f"'{n}'" for _, n in added))
                st.rerun()
            except ValueError as err:
                st.error(str(err))

# ================================================================ 2. holdings & P&L
with holdings_tab:
    live = has_keys()
    key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"], "Showing your PAPER positions until then.")
    mode = "live" if live else "paper"
    try:
        if live:
            from marketlens.broker.dhan_data import DhanDataClient
            client = DhanDataClient()
            rows = lib.add_quotes(lib.dhan_rows(client.get_holdings(), client.get_positions()), client.get_quote)
        else:
            rows = lib.paper_rows()
        rows = lib.stored_prev_close(rows, dt.datetime.now(IST).date())
        view = lib.holdings_view(rows)
    except Exception as err:
        view = lib.holdings_view(pd.DataFrame(columns=lib.HOLDING_COLUMNS))
        st.error(f"Could not read holdings: {err}")
    st.caption("Source: " + ("your **Dhan** account (holdings + today's positions)" if live else
                             "**paper** positions (virtual money). Latest price = the last price the paper broker saw."))
    totals = lib.holdings_totals(view)
    c = st.columns(4)
    c[0].metric("Invested", rs(totals["invested"]))
    c[1].metric("Current value", rs(totals["current_value"]))
    c[2].metric("Day P&L", rs(totals["day_pnl"]))
    c[3].metric("Total P&L", rs(totals["total_pnl"]), help="Open P&L plus realised P&L after costs")
    if view.empty:
        st.info("No open holdings or positions.")
    else:
        st.dataframe(view[["symbol", "kind", "strategy", "qty", "avg_price", "ltp", "invested", "current_value",
                           "day_pnl", "open_pnl", "total_pnl", "return_pct", "weight_pct", "sector"]],
                     hide_index=True, width="stretch")
        shares = lib.allocation(view)
        a1, a2 = st.columns([1.2, 1])
        fig = go.Figure(go.Pie(labels=shares["Sector"], values=shares["Value (Rs)"], hole=0.45, sort=False))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10), title="Allocation by sector")
        a1.plotly_chart(fig, width="stretch", key="lib_alloc")
        a2.dataframe(shares, hide_index=True, width="stretch")
        today = dt.datetime.now(IST).date()
        if st.session_state.get("lib_snap") != (mode, today):
            try:
                lib.save_snapshot(view, mode, today)
                st.session_state["lib_snap"] = (mode, today)
            except Exception as err:
                st.caption(f"Could not save today's snapshot: {err}")
    try:
        history = lib.snapshot_history(mode)
    except Exception:
        history = pd.DataFrame()
    st.markdown("**Portfolio value over time** (one snapshot a day, saved when this page is opened)")
    if len(history) < 2:
        st.caption("The chart appears once there are snapshots on two or more days.")
    else:
        fig = go.Figure()
        fig.add_scatter(x=history["date"], y=history["value"], name="Current value", line=dict(color=charts.STRATEGY_COLOR))
        fig.add_scatter(x=history["date"], y=history["invested"], name="Invested",
                        line=dict(color=charts.BENCHMARK_COLOR, dash="dot"))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), yaxis_tickprefix="Rs ",
                          legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, width="stretch", key="lib_history")

# ================================================================ 3. trade journal
with journal_tab:
    try:
        trades = lib.all_trades()
        jtable = lib.journal_table(trades, lib.load_journal())
    except Exception as err:
        trades = jtable = lib.journal_table(pd.DataFrame(columns=lib.TRADE_COLUMNS), pd.DataFrame())
        st.error(f"Could not read trades: {err}")
    if jtable.empty:
        st.info("No trades yet. Paper orders, forward tests (6), live orders (11) and simulator sessions (5) "
                "appear here.")
    else:
        f = st.columns([1.3, 1.3, 1, 1, 1])
        sources = f[0].multiselect("Source", list(SOURCE_NAMES), format_func=SOURCE_NAMES.get, key="j_src")
        strategies = f[1].multiselect("Strategy", sorted(jtable["strategy"].dropna().unique()), key="j_strat")
        start = f[2].date_input("From", min(jtable["date"]), key="j_from")
        end = f[3].date_input("To", max(jtable["date"]), key="j_to")
        result = f[4].selectbox("Result", ["all", "win", "loss", "open"], key="j_result",
                                format_func=lambda r: {"all": "All", "win": "Winners", "loss": "Losers",
                                                       "open": "Still open"}[r])
        g = st.columns([1, 2])
        symbol = g[0].text_input("Symbol contains", key="j_symbol")
        all_tags = sorted({t for tags in jtable["tags"] for t in tags})
        tags = g[1].multiselect("Tag", all_tags, key="j_tags")
        shown = lib.filter_trades(jtable, sources, strategies, start, end, symbol, tags, result)
        closed = shown[shown["status"] == "closed"]
        s = st.columns(4)
        s[0].metric("Trades", len(shown), f"{len(closed)} closed", delta_color="off")
        s[1].metric("Net P&L (closed)", rs(closed["net_pnl"].sum()))
        s[2].metric("Win rate", f"{(closed['net_pnl'] > 0).mean() * 100:.1f}%" if len(closed) else "-")
        s[3].metric("Costs", rs(shown["costs"].sum()))
        st.dataframe(shown.assign(tags=shown["tags"].map(", ".join),
                                  source=shown["source"].map(SOURCE_NAMES.get)).drop(columns=["key", "strategy_id"]),
                     hide_index=True, width="stretch", column_config={
                         "entry_time": st.column_config.DatetimeColumn("Entry", format="DD-MMM-YY HH:mm"),
                         "exit_time": st.column_config.DatetimeColumn("Exit", format="DD-MMM-YY HH:mm"),
                         "rating": st.column_config.NumberColumn("Rating", format="%d ⭐")})

        st.markdown("**Add notes to a trade**")
        options = list(shown["key"])
        if options:
            labels = {r.key: f"{r.date:%d-%b-%Y} · {r.symbol} {r.side} · "
                             f"{'open' if r.status == 'open' else rs(r.net_pnl)} · {SOURCE_NAMES[r.source]}"
                      for r in shown.itertuples()}
            key = st.selectbox("Trade", options, format_func=labels.get, key="j_pick")
            row = shown[shown["key"] == key].iloc[0]
            groups = lib.split_tags(row["tags"])
            with st.form(f"j_form_{key}"):
                notes = st.text_area("Notes", row["notes"] if isinstance(row["notes"], str) else "",
                                     placeholder="Why did you take it? What happened? What would you do again?")
                t = st.columns(3)
                setup = t[0].multiselect("Setup", sorted(set(lib.TAG_GROUPS["setup"] + groups["setup"])), groups["setup"],
                                         accept_new_options=True)
                mistake = t[1].multiselect("Mistakes", sorted(set(lib.TAG_GROUPS["mistake"] + groups["mistake"])),
                                           groups["mistake"], accept_new_options=True)
                emotion = t[2].multiselect("Emotion", sorted(set(lib.TAG_GROUPS["emotion"] + groups["emotion"])),
                                           groups["emotion"], accept_new_options=True)
                rating = st.radio("Rating (how well you followed your plan)", [0, 1, 2, 3, 4, 5], horizontal=True,
                                  index=int(row["rating"]) if pd.notna(row["rating"]) else 0,
                                  format_func=lambda n: "None" if n == 0 else "⭐" * n)
                shot = st.file_uploader("Screenshot (chart)", type=[e.strip(".") for e in lib.SCREENSHOT_TYPES])
                if st.form_submit_button("💾 Save note"):
                    try:
                        path = lib.save_screenshot(key, shot.name, shot.getvalue()) if shot is not None else None
                        lib.save_journal(row.to_dict(), notes, lib.make_tags(setup, mistake, emotion) + groups["other"],
                                         rating or None, path)
                        st.success("Saved.")
                        st.rerun()
                    except ValueError as err:
                        st.error(str(err))
            if isinstance(row["screenshot"], str) and Path(row["screenshot"]).exists():
                st.image(row["screenshot"], caption="Saved screenshot", width=520)

        st.markdown("**What works and what does not** (closed trades in the filter above)")
        by_tag, by_day, by_strategy = st.tabs(["By tag", "By weekday", "By strategy"])
        for tab, by in ((by_tag, "tag"), (by_day, "weekday"), (by_strategy, "strategy")):
            with tab:
                stats = lib.stats_by(shown, by)
                if stats.empty:
                    st.caption("Nothing to group yet." + (" Add tags to trades above." if by == "tag" else ""))
                    continue
                fig = go.Figure(go.Bar(x=stats.iloc[:, 0], y=stats["Net P&L"],
                                       marker_color=[charts.PROFIT_COLOR if v > 0 else charts.LOSS_COLOR
                                                     for v in stats["Net P&L"]]))
                fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis_tickprefix="Rs ")
                st.plotly_chart(fig, width="stretch", key=f"j_stats_{by}")
                st.dataframe(stats, hide_index=True, width="stretch")

# ================================================================ 4. performance
with perf_tab:
    try:
        trades = lib.all_trades()
    except Exception as err:
        trades = pd.DataFrame(columns=lib.TRADE_COLUMNS)
        st.error(f"Could not read trades: {err}")
    p = st.columns([2, 1])
    picked = p[0].multiselect("Include", list(SOURCE_NAMES), default=["paper", "forward", "live"],
                              format_func=SOURCE_NAMES.get, key="p_src",
                              help="Simulator trades are on replayed past days, so they are left out by default")
    capital = p[1].number_input("Capital (Rs)", 10_000.0, 1e10, float(config.PAPER_CAPITAL), 50_000.0, key="p_cap")
    chosen = trades[trades["source"].isin(picked)]
    closed = chosen[chosen["status"] == "closed"]
    bench = lib.nifty_closes(min(closed["date"]), max(closed["date"])) if len(closed) else None
    result = lib.performance(chosen, capital, bench)
    if result is None:
        st.info("No closed trades from these sources yet.")
    else:
        m = result.metrics
        c = st.columns(5)
        c[0].metric("Realised P&L", rs(m["total_pnl"]), f"{m['return_pct']:.2f}% of capital")
        c[1].metric("Win rate", f"{m['win_rate_pct']:.1f}%", f"{m['trades']} trades", delta_color="off")
        c[2].metric("Best day", rs(m["best_day"]))
        c[3].metric("Worst day", rs(m["worst_day"]))
        c[4].metric("Max drawdown", rs(m["max_drawdown_rs"]), f"{m['max_drawdown_pct']:.2f}%", delta_color="off")
        c = st.columns(4)
        c[0].metric("Profit factor", "-" if m["profit_factor"] is None else f"{m['profit_factor']:.2f}")
        c[1].metric("Average win / loss", f"{rs(m['avg_win'])} / {rs(m['avg_loss'])}")
        c[2].metric("Costs paid", rs(result.extras["costs"]))
        c[3].metric("Trading days", m["trading_days"])
        if bench is None:
            st.caption("Nifty 50 line: no stored NIFTYBEES prices (sync the ETF universe to add it).")
        st.plotly_chart(charts.equity_chart(result), width="stretch", key="p_equity")
        st.plotly_chart(charts.drawdown_chart(result), width="stretch", key="p_dd")
        st.plotly_chart(charts.monthly_heatmap(result), width="stretch", key="p_month")
        st.plotly_chart(charts.daily_heatmap(result), width="stretch", key="p_daily")
        st.dataframe(result.extras["by_source"].assign(source=lambda t: t["source"].map(SOURCE_NAMES.get)),
                     hide_index=True)
        st.caption("Realised = closed trades only, after brokerage, taxes and slippage, counted on the day they closed.")

historical_note()
