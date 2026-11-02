"""Tests for dashboards/analytics_library.py (module 10), run headless with Streamlit's AppTest.

Every database function of marketlens.library is replaced with a fake, so nothing is read or written.
"""
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from marketlens import library as lib
from marketlens.broker.candles import IST

PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "analytics_library.py")
T0 = dt.datetime(2026, 10, 26, 9, 20, tzinfo=IST)

STRATEGIES = {
    1: {"id": 1, "name": "Straddle 920", "kind": "options", "description": None, "tags": [], "is_active": True,
        "params": {"source": "options_backtester", "underlying": "NIFTY"}, "legs": []},
    2: {"id": 2, "name": "ETF top 2", "kind": "etf", "description": None, "tags": [], "is_active": True,
        "params": {"source": "etf_momentum"}, "legs": []},
}


# Three closed trades and one open one, in all_trades() layout.
def fake_trades() -> pd.DataFrame:
    rows = []
    for n, (source, pnl, day) in enumerate([("paper", 1200.0, 26), ("forward", -500.0, 27), ("simulator", 300.0, 20),
                                             ("paper", None, 28)]):
        when = T0.replace(day=day)
        rows.append({"key": f"order:{n + 1}", "source": source, "mode": "paper", "strategy_id": 1,
                     "strategy": "Straddle 920", "symbol": "RELIANCE", "side": "BUY", "qty": 10, "entry_time": when,
                     "entry_price": 100.0, "exit_time": when if pnl is not None else pd.NaT,
                     "exit_price": 110.0 if pnl is not None else None, "gross_pnl": pnl, "costs": 10.0,
                     "net_pnl": pnl, "status": "closed" if pnl is not None else "open", "date": when.date()})
    return pd.DataFrame(rows, columns=lib.TRADE_COLUMNS)


@pytest.fixture
def fakes(monkeypatch):
    calls = {"snapshots": [], "journal": [], "deleted": [], "renamed": [], "copied": []}
    table = pd.DataFrame([{"id": 1, "name": "Straddle 920", "type": "Options", "made_in": "Options Backtester (4)",
                           "source": "options_backtester", "created": T0, "updated": T0, "description": None,
                           "last_backtest": T0, "bt_total_pnl": 2500.0, "bt_cagr_pct": 12.0, "bt_max_dd_pct": -4.0,
                           "bt_sharpe": 1.2, "bt_win_rate_pct": 60.0, "bt_trades": 20, "bt_runs": 2, "fwd_days": 2,
                           "fwd_total_pnl": 500.0, "fwd_winning_days": 1, "fwd_last_day": T0.date(),
                           "fwd_status": "stopped"},
                          {"id": 2, "name": "ETF top 2", "type": "ETF", "made_in": "ETF Momentum (9)",
                           "source": "etf_momentum", "created": T0, "updated": T0, "description": None,
                           "last_backtest": None, "bt_total_pnl": None, "bt_cagr_pct": None, "bt_max_dd_pct": None,
                           "bt_sharpe": None, "bt_win_rate_pct": None, "bt_trades": None, "bt_runs": 0, "fwd_days": 0,
                           "fwd_total_pnl": None, "fwd_winning_days": 0, "fwd_last_day": None, "fwd_status": None}])
    holdings = pd.DataFrame([{"symbol": "TCS", "kind": "Holding", "qty": 2, "avg_price": 3000.0, "ltp": 3100.0,
                              "prev_close": 3050.0, "realized_pnl": 0.0, "opened_today": False,
                              "strategy": "Swing", "source": "PAPER"}])
    monkeypatch.setattr("marketlens.broker.dhan_client.has_keys", lambda: False)
    monkeypatch.setattr(lib, "list_library", lambda url=None: table)
    monkeypatch.setattr(lib, "get_strategy", lambda sid, url=None: STRATEGIES[sid])
    monkeypatch.setattr(lib, "duplicate_strategy", lambda sid, name=None, url=None: calls["copied"].append((sid, name)) or 1)
    monkeypatch.setattr(lib, "rename_strategy", lambda sid, name, url=None: calls["renamed"].append((sid, name)) or name)
    monkeypatch.setattr(lib, "delete_strategy", lambda sid, url=None: calls["deleted"].append(sid) or "x")
    monkeypatch.setattr(lib, "export_strategies", lambda ids, url=None: '{"format": "marketlens-strategies"}')
    monkeypatch.setattr(lib, "paper_rows", lambda url=None: holdings)
    monkeypatch.setattr(lib, "stored_prev_close", lambda rows, day=None, url=None: rows)
    monkeypatch.setattr(lib, "save_snapshot", lambda view, mode, day=None, url=None: calls["snapshots"].append(mode))
    monkeypatch.setattr(lib, "snapshot_history", lambda mode, url=None: pd.DataFrame(
        {"date": [dt.date(2026, 10, 27), dt.date(2026, 10, 28)], "invested": [6000.0, 6000.0],
         "value": [6100.0, 6200.0], "day_pnl": [50.0, 100.0]}))
    monkeypatch.setattr(lib, "all_trades", lambda url=None: fake_trades())
    monkeypatch.setattr(lib, "load_journal", lambda url=None: pd.DataFrame(
        [{"key": "order:1", "notes": "plan followed", "tags": ["setup:Breakout"], "rating": 4, "screenshot": None}]))
    monkeypatch.setattr(lib, "save_journal", lambda trade, notes="", tags=None, rating=None, screenshot_path=None,
                        url=None: calls["journal"].append((trade["key"], notes, tags, rating)) or 1)
    monkeypatch.setattr(lib, "nifty_closes", lambda start, end, url=None: None)
    return calls


# Open the page.
def open_page() -> AppTest:
    return AppTest.from_file(PAGE, default_timeout=120).run()


def test_library_tab_and_actions(fakes):
    at = open_page()
    assert not at.exception
    assert at.selectbox(key="lib_pick").value == 1
    at.button(key="lib_bt").click().run()
    handoff = at.session_state["handoff"]
    assert handoff["to"] == "backtest_saved" and handoff["strategy_id"] == 1
    at.button(key="lib_fwd").click().run()
    assert at.session_state["f_pick"] == 1
    at.selectbox(key="lib_pick").set_value(2).run()                       # ETF: no simulator or forward test
    assert at.button(key="lib_fwd").disabled and at.button(key="lib_sim").disabled
    assert not at.button(key="lib_bt").disabled
    at.button(key="lib_live").click().run()
    assert at.session_state["handoff"]["to"] == "live"


def test_duplicate_rename_delete(fakes):
    at = open_page()
    at.text_input(key="lib_ren_name").set_value("Straddle A").run()
    at.button(key="lib_ren").click().run()
    assert fakes["renamed"] == [(1, "Straddle A")]
    at.button(key="lib_dup").click().run()
    assert fakes["copied"] == [(1, "Straddle 920 (copy)")]
    assert at.button(key="lib_del").disabled                              # needs the "Yes, delete" tick
    at.checkbox(key="lib_del_sure").check().run()
    at.button(key="lib_del").click().run()
    assert fakes["deleted"] == [1]


def test_holdings_tab(fakes):
    at = open_page()
    labels = {m.label: m.value for m in at.metric}
    assert labels["Invested"] == "Rs 6,000" and labels["Current value"] == "Rs 6,200"
    assert labels["Day P&L"] == "Rs 100" and fakes["snapshots"] == ["paper"]
    at.run()
    assert fakes["snapshots"] == ["paper"]                                # saved once a day, not on every rerun


def test_journal_save_and_stats(fakes):
    at = open_page()
    assert at.metric[4].label == "Trades"
    at.multiselect(key="j_src").set_value(["paper"]).run()
    assert any(m.label == "Trades" and m.value == "2" for m in at.metric)
    at.selectbox(key="j_pick").set_value("order:1").run()
    at.text_area[0].set_value("Waited for the retest")
    next(b for b in at.button if b.label == "💾 Save note").click().run()
    key, notes, tags, rating = fakes["journal"][0]
    assert key == "order:1" and notes == "Waited for the retest" and tags == ["setup:Breakout"] and rating == 4


def test_performance_tab(fakes):
    at = open_page()
    values = {m.label: m.value for m in at.metric}
    assert values["Realised P&L"] == "Rs 700"                             # paper + forward, simulator left out
    assert values["Best day"] == "Rs 1,200" and values["Worst day"] == "Rs -500"
    at.multiselect(key="p_src").set_value(["paper", "forward", "live", "simulator"]).run()
    assert {m.label: m.value for m in at.metric}["Realised P&L"] == "Rs 1,000"
