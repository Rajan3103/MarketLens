"""Tests for dashboards/forward_test.py (module 6), run headless with Streamlit's AppTest.

The page only starts / stops the background process and shows its status; those functions are
replaced here, so no process is started and no database is needed.
"""
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from marketlens.trading import forward as fw

PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "forward_test.py")
STRATEGIES = pd.DataFrame([{"id": 3, "name": "920 straddle", "kind": "options", "underlying": "NIFTY"},
                           {"id": 4, "name": "EMA SBIN", "kind": "indicator", "underlying": "SBIN"}])
RUNNING = {"run_id": 9, "day": dt.date(2026, 10, 28), "status": "running", "state": "in_trade",
           "state_text": "In trade", "message": "2 leg(s) open.", "pnl": -1234.5, "heartbeat_age": 12.0, "alive": True,
           "params": {"market_time": "10:05",
                      "legs": [{"leg": 1, "symbol": "NIFTY 25000 CE", "side": "SELL", "qty": 65, "pnl": -500}],
                      "trades": [{"leg": 2, "symbol": "NIFTY 25000 PE 03-Nov-2026", "exit_reason": "SL",
                                  "paper_pnl": -734.5}]}}


@pytest.fixture
def calls(monkeypatch):
    seen = {"start": [], "stop": [], "status": RUNNING}
    monkeypatch.setattr(fw, "list_strategies", lambda url=None: STRATEGIES)
    monkeypatch.setattr(fw, "status", lambda sid, url=None: seen["status"])
    monkeypatch.setattr(fw, "start_process", lambda sid, capital=None: seen["start"].append((sid, capital)) or 4321)
    monkeypatch.setattr(fw, "request_stop", lambda sid, url=None: seen["stop"].append(sid) or True)
    monkeypatch.setattr(fw, "history", lambda sid, url=None: pd.DataFrame(
        {"date": [dt.date(2026, 10, 27), dt.date(2026, 10, 28)], "forward_pnl": [1500.0, -1234.5],
         "state": ["done", "in_trade"], "trades": [2, 1], "slippage": [0.4, 1.1], "backtest_pnl": [1620.0, None],
         "difference": [-120.0, None]}))
    return seen


def test_running_status_and_stop(calls):
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception
    assert any(m.label == "Status" and m.value == "In trade" for m in at.metric)
    assert any(m.label.startswith("Today's P&L") and "1,234" in m.value for m in at.metric)
    assert at.button(key="f_start").disabled and not at.button(key="f_stop").disabled
    assert any("Stop loss" in str(df.value) or "SL" in str(df.value) for df in at.dataframe)
    at.button(key="f_stop").click().run()
    assert calls["stop"] == [3] and any("Stop requested" in w.value for w in at.warning)


def test_start_when_not_running(calls):
    calls["status"] = None
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.button(key="f_start").disabled and at.button(key="f_stop").disabled
    at.selectbox(key="f_strategy").set_value(4)
    at.number_input(key="f_capital").set_value(200_000.0)
    at.button(key="f_start").click().run()
    assert calls["start"] == [(4, 200_000.0)] and any("4321" in s.value for s in at.success)


def test_history_tab(calls):
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    tables = [df.value for df in at.dataframe]
    assert any("backtest_pnl" in t.columns and len(t) == 2 for t in tables)
    assert any("Why the two differ" in c.value for c in at.caption)


def test_quick_indicator_strategy(calls, monkeypatch):
    saved = {}
    monkeypatch.setattr(fw, "save_indicator_strategy", lambda name, s, url=None: saved.update(name=name, s=s) or 11)
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    at.text_input(key="q_symbol").set_value("reliance")
    at.button(key="q_save").click().run()
    assert saved["s"].symbol == "RELIANCE" and saved["s"].rule == "ema_cross" and saved["s"].qty == 10
    assert any("Saved" in s.value for s in at.success)


def test_no_strategies(monkeypatch):
    monkeypatch.setattr(fw, "list_strategies", lambda url=None: STRATEGIES.iloc[0:0])
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert any("No strategies to run yet" in i.value for i in at.info)


# The page offers the DEMO practice strategy when it is not in the library yet.
def test_add_demo_strategy(calls, monkeypatch):
    added = []
    monkeypatch.setattr(fw, "ensure_demo_strategy", lambda url=None: added.append(1) or 7)
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    at.button(key="f_add_demo").click().run()
    assert added == [1] and not at.exception
