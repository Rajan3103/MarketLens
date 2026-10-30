"""Tests for dashboards/options_backtester.py (module 4), run headless with Streamlit's AppTest.

The page reads option minutes through options_bt.StoredOptionData; the tests swap it for a fake
holding 5 made-up DEMO days, so no database is needed (saving a run is also replaced).
"""
import datetime as dt
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from marketlens.backtest import options_bt as ob
from test_options_bt import YearOfMinutes

PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "options_backtester.py")


class FiveDays(YearOfMinutes):
    """The first 5 trading days of 2025 (made-up DEMO minutes)."""

    def __init__(self):
        super().__init__()
        self.days = self.days[:5]


class NoData:
    """A database with no option minutes."""

    def __init__(self, *args, **kw):
        pass

    def trading_days(self, start, end):
        return []

    def load_day(self, day, expiry):
        raise AssertionError("nothing should be loaded")


# Swap the data source and the run saver; clear the page's cache between tests.
@pytest.fixture
def fake_db(monkeypatch):
    data = FiveDays()
    monkeypatch.setattr(ob, "StoredOptionData", lambda *a, **k: data)
    monkeypatch.setattr("marketlens.backtest.store.save_run", lambda *a, **k: 7)
    st.cache_data.clear()
    return data


# Open the page.
def open_page() -> AppTest:
    return AppTest.from_file(PAGE, default_timeout=120).run()


# Number of leg rows on the page.
def leg_count(at: AppTest) -> int:
    return len(at.session_state["bt_legs"])


def test_defaults_and_run(fake_db):
    at = open_page()
    assert not at.exception and leg_count(at) == 2                     # the 9:20 straddle
    assert at.date_input(key="start_1").value == dt.date(2025, 1, 1)    # dates follow the stored data
    at.button(key="bt_run").click().run()
    assert not at.exception
    result = at.session_state["bt_result"]
    assert result.extras["days_traded"] == 5 and result.extras["run_id"] == 7
    labels = {m.label for m in at.metric}
    assert {"Total P&L", "Win rate", "Max drawdown"} <= labels
    assert any("DEMO DATA" in w.value for w in at.warning)               # DEMO banner
    assert any("Why trades exited" in m.value for m in at.markdown)
    assert any("not a live trading recommendation" in c.value for c in at.caption)


def test_templates(fake_db):
    at = open_page()
    for name, legs in (("iron_condor", 4), ("breakout_buying", 2), ("delta_strangle", 2)):
        at.selectbox(key="bt_template").set_value(name).run()
        at.button[0].click().run()                                      # "Load template"
        assert not at.exception and leg_count(at) == legs
    assert at.radio(key=f"entry_mode_{at.session_state['bt_ver']}").value == "time"
    at.selectbox(key="bt_template").set_value("breakout_buying").run()
    at.button[0].click().run()
    assert at.radio(key=f"entry_mode_{at.session_state['bt_ver']}").value == "breakout"
    at.button(key="bt_run").click().run()
    assert not at.exception and at.session_state["bt_result"] is not None


def test_add_duplicate_remove(fake_db):
    at = open_page()
    at.radio(key="quick_kind_1").set_value("custom").run()          # the leg buttons are in Custom legs
    first = at.session_state["bt_legs"][0]["uid"]
    at.number_input(key=f"leg_{first}_sl").set_value(40.0).run()
    at.button(key=f"leg_{first}_dup").click().run()
    assert leg_count(at) == 3
    copy = at.session_state["bt_legs"][1]
    assert copy["sl"] == 40.0 and copy["uid"] != first
    at.button(key=f"leg_{first}_del").click().run()
    assert leg_count(at) == 2 and at.session_state["bt_legs"][0]["uid"] == copy["uid"]
    next(b for b in at.button if b.label == "➕ Add leg").click().run()
    assert leg_count(at) == 3 and not at.exception


# A wrong setting is shown instead of the Run button.
def test_settings_problem_shown(fake_db):
    at = open_page()
    for i in range(5):
        at.checkbox(key=f"day_{i}_1").uncheck()
    at.run()
    assert any("weekdays" in e.value for e in at.error)
    assert not any(b.key == "bt_run" for b in at.button)


# No stored data -> a warning and the button to the Options Data Downloader.
def test_no_data(monkeypatch):
    monkeypatch.setattr(ob, "StoredOptionData", NoData)
    st.cache_data.clear()
    at = open_page()
    at.button(key="bt_run").click().run()
    assert any("Download them first" in w.value for w in at.warning)
    assert any(b.key == "bt_to_downloader" for b in at.button)


# Legs from the Strategy builder become ATM +/- N rules.
def test_builder_handoff(fake_db):
    at = AppTest.from_file(PAGE, default_timeout=120)
    at.session_state["handoff"] = {"to": "backtest", "name": "My condor", "underlying": "NIFTY", "spot": 25010,
                                   "legs": [{"side": "SELL", "option_type": "CE", "strike": 25100, "lots": 1},
                                            {"side": "BUY", "option_type": "CE", "strike": 25200, "lots": 1}]}
    at.run()
    at.button(key="bt_use_builder").click().run()
    legs = at.session_state["bt_legs"]
    assert [(x["side"], x["value"]) for x in legs] == [("SELL", 2), ("BUY", 4)]


# Saving to the Strategy Library passes the settings that were run.
def test_save_to_library(fake_db, monkeypatch):
    saved = {}
    monkeypatch.setattr(ob, "save_backtest_strategy", lambda name, settings: saved.update(name=name, s=settings) or 3)
    at = open_page()
    at.button(key="bt_run").click().run()
    at.text_input(key="bt_save_name").set_value("Straddle 920").run()
    at.button(key="bt_save").click().run()
    assert saved["name"] == "Straddle 920" and len(saved["s"].legs) == 2
    assert any("Saved 'Straddle 920'" in s.value for s in at.success)


# ---- the strategy type radio: short straddle / short strangle / premium based ----

# The legs the page would run (the settings built from the boxes).
def page_legs(at: AppTest):
    at.button(key="bt_run").click().run()
    return [(leg.side, leg.option_type, leg.strike_rule, leg.value) for leg in at.session_state["bt_settings"].legs]


def test_strategy_type_radio(fake_db):
    at = open_page()
    assert at.radio(key="quick_kind_1").value == "straddle"                     # the 9:20 straddle opens first
    assert page_legs(at) == [("SELL", "CE", "ATM", 0), ("SELL", "PE", "ATM", 0)]
    assert not any(b.key and b.key.endswith("_dup") for b in at.button)         # no leg buttons in quick types
    # Short strangle: CE ATM+3, PE ATM-2 (both out of the money)
    at.radio(key="quick_kind_1").set_value("strangle").run()
    assert at.selectbox(key="q_ce_offset_1").options == [f"ATM+{n}" for n in range(1, 11)]
    assert at.selectbox(key="q_pe_offset_1").options == [f"ATM-{n}" for n in range(1, 11)]
    at.selectbox(key="q_ce_offset_1").set_value(3)
    at.selectbox(key="q_pe_offset_1").set_value(2).run()
    assert page_legs(at) == [("SELL", "CE", "ATM", 3), ("SELL", "PE", "ATM", -2)]
    # Premium based: the strikes nearest Rs 100 (CE) and Rs 120 (PE)
    at.radio(key="quick_kind_1").set_value("premium").run()
    at.number_input(key="q_ce_premium_1").set_value(100.0)
    at.number_input(key="q_pe_premium_1").set_value(120.0).run()
    assert page_legs(at) == [("SELL", "CE", "PREMIUM", 100.0), ("SELL", "PE", "PREMIUM", 120.0)]
    assert not at.exception and at.session_state["bt_result"].extras["days_traded"] == 5


# The SL typed on a leg stays when the strategy type changes; extra legs go when leaving Custom.
def test_switching_type_keeps_leg_settings(fake_db):
    at = open_page()
    at.selectbox(key="bt_template").set_value("iron_condor").run()
    at.button[0].click().run()                                                   # Load template
    v = at.session_state["bt_ver"]
    assert at.radio(key=f"quick_kind_{v}").value == "custom" and leg_count(at) == 4
    ce = at.session_state["bt_legs"][0]["uid"]
    at.number_input(key=f"leg_{ce}_sl").set_value(35.0).run()
    at.radio(key=f"quick_kind_{v}").set_value("straddle").run()
    assert leg_count(at) == 2
    legs = at.session_state["bt_legs"]
    assert legs[0]["uid"] == ce and at.number_input(key=f"leg_{ce}_sl").value == 35.0
    assert page_legs(at) == [("SELL", "CE", "ATM", 0), ("SELL", "PE", "ATM", 0)]


# Templates set the radio: the Rs 50 premium strangle opens as "Premium based" with 50 / 50.
def test_template_sets_type(fake_db):
    at = open_page()
    at.selectbox(key="bt_template").set_value("premium_strangle").run()
    at.button[0].click().run()
    v = at.session_state["bt_ver"]
    assert at.radio(key=f"quick_kind_{v}").value == "premium"
    assert at.number_input(key=f"q_ce_premium_{v}").value == 50.0 and at.number_input(key=f"q_pe_premium_{v}").value == 50.0
