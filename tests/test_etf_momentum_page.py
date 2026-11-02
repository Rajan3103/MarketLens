"""Tests for dashboards/etf_momentum.py (module 9), run headless with Streamlit's AppTest.

The page's price loader is swapped for made-up ETF prices, so no database is needed
(saving to the Strategy Library is also replaced).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from marketlens.momentum import etf_bt as eb

PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "etf_momentum.py")
DAYS = pd.bdate_range("2019-01-01", "2023-12-29")


# Made-up prices for every ETF in etfs.csv; SILVERBEES lists late (2022).
def fake_data() -> eb.EtfData:
    universe = eb.etf_universe()
    rng = np.random.default_rng(4)
    closes = {s: 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(DAYS))) for s in universe["symbol"]}
    frame = pd.DataFrame(closes, index=DAYS)
    frame.loc[:"2021-12-31", "SILVERBEES"] = np.nan
    return eb.EtfData(frame, dict(zip(universe["symbol"], universe["category"])))


@pytest.fixture
def page_data(monkeypatch):
    monkeypatch.setattr(eb, "load_etf_data", lambda *a, **k: fake_data())
    st.cache_resource.clear()


# Open the page.
def open_page() -> AppTest:
    return AppTest.from_file(PAGE, default_timeout=120).run()


def test_run_backtest(page_data, monkeypatch):
    saved = {}
    monkeypatch.setattr(eb, "save_etf_strategy", lambda name, s: saved.update(name=name, s=s) or 5)
    monkeypatch.setattr("marketlens.backtest.store.save_run", lambda sid, params, result: saved.update(run=sid) or 9)
    at = open_page()
    assert not at.exception and not at.error
    assert any("not a promise of future returns" in i.value for i in at.info)
    at.date_input(key="etf_start_1").set_value(pd.Timestamp("2021-01-01").date())
    at.button(key="etf_run").click().run()
    assert not at.exception
    result = at.session_state["etf_result"]
    assert result.extras["orders"] > 0 and not result.is_demo
    labels = {m.label for m in at.metric}
    assert {"Final value", "CAGR", "Max drawdown", "Capital gains tax paid", "If all sold at the end"} <= labels
    at.button(key="etf_save").click().run()
    assert saved["s"].top_k == 2 and saved["run"] == 5 and any("Saved" in s.value for s in at.success)


def test_claim_sweep_and_signal(page_data):
    at = open_page()
    at.date_input(key="etf_start_1").set_value(pd.Timestamp("2021-01-01").date())
    at.date_input(key="etf_claim_start").set_value(pd.Timestamp("2021-01-01").date())
    at.button(key="etf_claim_run").click().run()
    assert not at.exception
    table = at.session_state["etf_claim_out"]["table"]
    assert table["Line"].iloc[0] == "Claim" and len(table) == 6
    at.button(key="etf_sweep_run").click().run()
    sweep = at.session_state["etf_sweep"]
    assert len(sweep) == 30 and set(sweep["Top K"]) == {1, 2, 3, 4, 5}
    assert any("Ranked at the close" in m.value for m in at.markdown)


def test_bad_settings_are_shown(page_data):
    at = open_page()
    at.multiselect(key="etf_lb_1").set_value([]).run()
    assert any("weight" in e.value for e in at.error)
    assert not any(b.key == "etf_run" for b in at.button)


def test_demo_when_nothing_is_stored(monkeypatch):
    def empty(*a, **k):
        raise ValueError("No stored ETF prices")
    monkeypatch.setattr(eb, "load_etf_data", empty)
    st.cache_resource.clear()
    at = open_page()
    assert any("DEMO DATA" in w.value for w in at.warning) and not at.exception


# A strategy opened from the Strategy Library fills the form with its settings.
def test_open_from_library(page_data):
    saved = eb.EtfBacktestSettings(symbols=["GOLDBEES", "MON100", "NIFTYBEES"], top_k=3, lookbacks=[126],
                                   weights=[1], rebalance="weekly", safe_asset="CASH",
                                   start=pd.Timestamp("2021-06-01").date())
    at = AppTest.from_file(PAGE, default_timeout=120)
    at.session_state["handoff"] = {"to": "etf", "name": "Gold rotation", "params": saved.to_params()}
    at.run()
    v = at.session_state["etf_ver"]
    assert v == 2 and "handoff" not in at.session_state
    assert at.slider(key=f"etf_k_{v}").value == 3 and at.radio(key=f"etf_reb_{v}").value == "weekly"
    assert at.multiselect(key=f"etf_lb_{v}").value == [126] and at.radio(key=f"etf_safe_{v}").value == "CASH"
    assert any("Gold rotation" in s.value for s in at.success)
    at.button(key="etf_run").click().run()
    assert set(at.session_state["etf_settings"].symbols) == {"GOLDBEES", "MON100", "NIFTYBEES", "LIQUIDBEES"}
