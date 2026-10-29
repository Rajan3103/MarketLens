"""Tests for dashboards/option_simulator.py (module 5), run headless with Streamlit's AppTest.

The page reads stored days through marketlens.simulator.replay; the tests swap those functions for
the hand-made day from test_options_bt_rules, so no database is needed (saving is also replaced).
"""
import datetime as dt
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from marketlens.backtest.options_bt import DayArrays
from marketlens.simulator import replay as rp
from test_options_bt_rules import DAY, EXPIRY, day_frame
from test_replay import CANDLES

PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "option_simulator.py")


@pytest.fixture
def fake_days(monkeypatch):
    monkeypatch.setattr(rp, "available_days", lambda u, url=None: [DAY])
    monkeypatch.setattr(rp, "available_expiries", lambda u, d, url=None: [EXPIRY])
    monkeypatch.setattr(rp, "load_day", lambda u, d, e=None, url=None: DayArrays.from_frame(day_frame(CANDLES), DAY, EXPIRY))
    saved = {}
    monkeypatch.setattr(rp, "save_session", lambda sim, notes="", url=None: saved.setdefault("id", 5))
    monkeypatch.setattr(rp, "list_sessions", lambda *a, **k: __import__("pandas").DataFrame())
    st.cache_data.clear()
    return saved


# Open the page and load the day starting at 09:17, with lot size 10.
def loaded() -> AppTest:
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    at.time_input(key="s_start").set_value(dt.time(9, 17))
    at.number_input(key="s_lot").set_value(10)
    next(b for b in at.button if "Load this day" in b.label).click().run()
    assert not at.exception
    return at


def test_load_trade_step_exit_save(fake_days):
    at = loaded()
    sim = at.session_state["sim"]
    assert sim.clock.time() == dt.time(9, 17)
    assert any(m.label == "Replay time" and m.value == "09:17" for m in at.metric)
    # Sell the ATM call with a 20-point SL at 09:17: filled at 100 (the 09:16 candle's close)
    at.radio(key="o_side").set_value("SELL")
    at.number_input(key="o_sl").set_value(20.0)
    at.button(key="o_place").click().run()
    pos = at.session_state["sim"].positions[0]
    assert (pos.side, pos.strike, pos.entry_price, pos.sl) == ("SELL", 26000, 100, 120)
    at.button(key="c_1").click().run()                               # 09:18: close 105
    assert at.session_state["sim"].pnl() < 0 and pos.is_open
    at.button(key="c_1").click().run()                               # candle 3 high 125 -> SL
    assert pos.reason == "SL" and pos.exit_price == 120
    # Another trade, then Exit all
    at.button(key="o_place").click().run()
    at.button(key="x_all").click().run()
    assert all(not p.is_open for p in at.session_state["sim"].positions)
    at.button(key="sim_save").click().run()
    assert fake_days["id"] == 5 and any("Saved as session 5" in s.value for s in at.success)


# Jump and the slider move the clock; going back with trades is refused with a message.
def test_jump_and_slider(fake_days):
    at = loaded()
    # (the hand-made day has candles until 09:28, so the test stays in the morning)
    at.time_input(key="c_jump_to").set_value(dt.time(9, 35))
    at.button(key="c_go").click().run()
    assert at.session_state["sim"].clock.time() == dt.time(9, 35)
    at.slider(key="sim_slider").set_value(dt.time(9, 45)).run()
    assert at.session_state["sim"].clock.time() == dt.time(9, 45)
    at.button(key="o_place").click().run()
    at.slider(key="sim_slider").set_value(dt.time(9, 30)).run()
    assert at.session_state["sim"].clock.time() == dt.time(9, 45)
    assert any("new session" in e.value for e in at.error)


# Play starts the clock (the page reruns with a timer), Pause stops it; the end shows a summary.
def test_play_pause_and_end(fake_days):
    at = loaded()
    at.button(key="c_play").click().run()
    assert at.session_state["sim"].clock.playing
    at.button(key="c_play").click().run()
    assert not at.session_state["sim"].clock.playing
    at.time_input(key="c_jump_to").set_value(dt.time(15, 30))
    at.button(key="c_go").click().run()
    assert at.session_state["sim"].clock.finished
    assert any("The day is over" in s.value for s in at.success)
    assert any(m.label == "Final P&L" for m in at.metric)


# No stored days: a warning and the button to the downloader.
def test_no_days(monkeypatch):
    monkeypatch.setattr(rp, "available_days", lambda u, url=None: [])
    monkeypatch.setattr(rp, "list_sessions", lambda *a, **k: __import__("pandas").DataFrame())
    st.cache_data.clear()
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert any("No NIFTY option minutes" in w.value for w in at.warning)
    assert any(b.key == "s_to_downloader" for b in at.button)
