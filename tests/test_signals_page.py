"""Tests for dashboards/signals_builder.py (module 7), run headless with Streamlit's AppTest.

Candles come from a fake loader, and saving / scanning / webhook events are replaced, so no
internet and no database are needed.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from marketlens.signals import backtest as sb
from marketlens.signals import pine
from marketlens.signals import rules as sr
from marketlens.webhook import payload as wh
from test_signal_rules import candles

PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "signals_builder.py")


@pytest.fixture
def fakes(monkeypatch):
    rng = np.random.default_rng(1)
    closes = 100 + rng.normal(0, 1.5, 300).cumsum()
    data = candles([(c, c + 1, c - 1, c) for c in closes])
    data.attrs["source"] = "DEMO"
    seen = {}
    monkeypatch.setattr(sb, "load_candles", lambda *a, **k: data)
    monkeypatch.setattr(sb, "scan", lambda s, symbols, **k: seen.setdefault("scan", (s, symbols)) and pd.DataFrame(
        {"symbol": ["AAA", "BBB"], "date": [None, None], "close": [1.0, 2.0], "signal": [True, False], "note": ["", ""]}))
    monkeypatch.setattr(sr, "save_signal_strategy",
                        lambda name, s, url=None, auto=False: seen.setdefault("saved", (name, s, auto)) and 12)
    monkeypatch.setattr(sr, "list_signal_strategies", lambda url=None: pd.DataFrame(columns=["id", "name", "params"]))
    monkeypatch.setattr(wh, "recent_events", lambda limit=50, url=None: pd.DataFrame(
        [{"received": None, "source": "tradingview", "processed": True, "error": None, "message": {"secret": "***"}}]))
    return seen


def test_build_and_backtest(fakes):
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception
    at.button(key="sb_run").click().run()
    assert not at.exception
    strategy, _, result, markers = at.session_state["sb_result"]
    assert strategy.entry[0].label() == "EMA(9) crosses above EMA(21)" and len(result.trades) > 0
    assert any(m.label == "Total P&L" for m in at.metric)
    assert any("DEMO DATA" in w.value for w in at.warning)


def test_add_and_remove_conditions(fakes):
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    at.button(key="sb_add_sb_entry").click().run()
    assert len(at.session_state["sb_entry"]) == 2
    uid = at.session_state["sb_entry"][1]["uid"]
    at.selectbox(key=f"sb_{uid}_r_kind").set_value("number").run()
    at.number_input(key=f"sb_{uid}_r_number_value").set_value(500.0).run()
    at.button(key="sb_run").click().run()
    strategy = at.session_state["sb_result"][0]
    assert strategy.entry[1].label() == "Close > 500" and strategy.entry_join == "AND"
    at.button(key=f"sb_{uid}_del").click().run()
    assert len(at.session_state["sb_entry"]) == 1


def test_save_scan_pine_and_webhook(fakes):
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    at.text_input(key="sb_save_name").set_value("EMA cross").run()
    at.checkbox(key="sb_auto").check().run()
    at.button(key="sb_save").click().run()
    assert fakes["saved"][0] == "EMA cross" and fakes["saved"][2] is True
    assert any("Saved 'EMA cross'" in s.value and "auto paper orders" in s.value for s in at.success)
    at.button(key="sc_run").click().run()
    assert len(fakes["scan"][1]) == 50                                 # Nifty 50
    assert any("not a recommendation" in s.value for s in at.success)
    codes = [c.value for c in at.code]
    script = next(c for c in codes if c.startswith("//@version=6") and "mlJson" in c)
    assert pine.validate_pine(script) == []
    assert any("/webhook/tradingview" in c for c in codes)
    assert any('"secret": "PASTE_YOUR_WEBHOOK_SECRET"' in c for c in codes)
    assert any("Checked: //@version=6" in s.value for s in at.success)


def test_problem_shown(fakes):
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    uid = at.session_state["sb_entry"][0]["uid"]
    at.button(key=f"sb_{uid}_del").click().run()
    assert any("Add at least one entry condition" in e.value for e in at.error)
