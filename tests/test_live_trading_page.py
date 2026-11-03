"""Tests for dashboards/live_trading.py (module 11), run headless with Streamlit's AppTest.

Every function the page calls is replaced by a stand-in: no database, no process started, no order sent.
"""
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from marketlens import config
from marketlens.broker.candles import IST
from marketlens.trading import forward as fw
from marketlens.trading import live_runner as lr
from marketlens.trading import monitor as mon
from marketlens.trading import order_service as os_

PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "live_trading.py")
NOW = dt.datetime.now(IST)
STRATEGIES = pd.DataFrame([{"id": 3, "name": "920 straddle", "kind": "options", "underlying": "NIFTY"}])
DEPLOYED = pd.DataFrame([{"strategy_id": 3, "name": "920 straddle", "mode": "paper", "status": "running",
                          "state": "in_trade", "lots_multiplier": 1, "max_loss_rs": 1000.0, "confirm": True,
                          "pnl": -450.0, "trade_date": NOW.date(), "heartbeat_at": NOW, "message": "2 leg(s) open."}])


class FakeService:
    """Stands in for OrderService: remembers what the buttons asked for."""
    calls = []

    def __init__(self, *a, **k):
        pass

    def kill(self, reason="", square_off=False, **k):
        FakeService.calls.append(("kill", square_off))
        return "closed"

    def unkill(self, reason=""):
        FakeService.calls.append(("unkill",))

    def confirm_signal(self, signal_id):
        FakeService.calls.append(("confirm", signal_id))
        return os_.SignalResult(signal_id, "SENT", message="All legs sent")

    def reject_signal(self, signal_id):
        FakeService.calls.append(("reject", signal_id))
        return os_.SignalResult(signal_id, "CANCELLED", message="Rejected by you")


@pytest.fixture
def fakes(monkeypatch):
    seen = {"deploy": [], "status": [], "all": [], "kill": (False, ""), "check": {
        "ok_live": False, "has_backtest": True, "forward_days": 4, "missing": ["6 more forward-test day(s)"]}}
    FakeService.calls = []
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (True, "ok"))
    monkeypatch.setattr(mon, "kill_state", lambda *a, **k: seen["kill"])
    monkeypatch.setattr(mon, "login_today", lambda *a, **k: {"status": "expired", "message": "DH-901",
                                                             "expiry": None, "checked_at": NOW})
    monkeypatch.setattr(mon, "webhook_status", lambda *a, **k: (False, "Not running"))
    monkeypatch.setattr(lr, "heartbeats", lambda url=None: {lr.RUNNER: {"pid": 1, "beat_at": NOW, "message": "ok"}})
    monkeypatch.setattr(lr, "runner_alive", lambda url=None: (True, 5.0))
    monkeypatch.setattr(fw, "list_strategies", lambda url=None: STRATEGIES)
    monkeypatch.setattr(lr, "deploy_check", lambda sid, url=None: seen["check"])
    monkeypatch.setattr(lr, "deploy", lambda *a, **k: seen["deploy"].append(a) or "'920 straddle' deployed in PAPER mode.")
    monkeypatch.setattr(lr, "list_deployments", lambda url=None: DEPLOYED)
    monkeypatch.setattr(lr, "set_status", lambda sid, status, url=None: seen["status"].append((sid, status)) or True)
    monkeypatch.setattr(lr, "set_all", lambda status, message, url=None, only=None: seen["all"].append(status) or 1)
    monkeypatch.setattr(mon, "waiting_signals", lambda *a, **k: pd.DataFrame(
        [{"signal_id": 12, "placed_at": "10:01:00", "strategy": "920 straddle", "legs": "SELL 65 NIFTY 25000 CE",
          "reason": "forward:E|1|09:20", "minutes_waiting": 1.5}]))
    monkeypatch.setattr(mon, "open_positions", lambda *a, **k: (pd.DataFrame(
        [{"strategy": "920 straddle", "symbol": "NIFTY 25000 CE", "qty": -65}]), "Paper positions"))
    monkeypatch.setattr(mon, "orders_today", lambda *a, **k: pd.DataFrame(
        [{"placed_at": "09:20:03", "symbol": "NIFTY 25000 CE", "status": "FILLED", "broker_order_id": None,
          "reason": "forward:E|1|09:20"}]))
    monkeypatch.setattr(mon, "loss_limits", lambda *a, **k: (
        {"pnl": -450.0, "limit": 2000.0, "used": 0.225},
        pd.DataFrame([{"strategy_id": 3, "strategy": "920 straddle", "pnl": -450.0, "limit": 1000.0, "used": 0.45}])))
    monkeypatch.setattr(mon, "error_modules", lambda url=None: ["marketlens.trading.order_service"])
    monkeypatch.setattr(mon, "error_log", lambda *a, **k: pd.DataFrame(
        [{"ts": "03-Nov 10:00:00", "level": "ERROR", "module": "marketlens.trading.order_service",
          "message": "Signal 9: exchange down", "details": ""}]))
    monkeypatch.setattr(os_, "OrderService", FakeService)
    return seen


# Run the page headless.
def page():
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


# Banner, status cards, deploy checks, tables, limits and errors all show.
def test_page_shows_everything(fakes):
    at = page()
    html = " ".join(m.value for m in at.markdown)
    assert "PAPER: VIRTUAL MONEY" in html and "AUTO_TRADE off" in html and "Kill switch off" in html
    assert "Dhan login today" in html and "expired" in html and "Not built yet (module 12)" in html
    assert any("Forward-test days: **4 / 10**" in m.value for m in at.markdown)
    assert any("before LIVE it still needs" in i.value for i in at.info)
    assert any("Signal 12" in m.value for m in at.markdown)
    assert any("Today's P&L Rs -450" in p.proto.text for p in at.get("progress"))
    assert len(at.dataframe) >= 4                                       # running, positions, orders, errors


# Deploy, Pause, Stop and Confirm call the right functions.
def test_buttons(fakes):
    at = page()
    at.button(key="lt_deploy").click().run()
    assert fakes["deploy"] and fakes["deploy"][0][0] == 3
    at.button(key="lt_pause_3").click().run()
    at.button(key="lt_stop_3").click().run()
    assert fakes["status"] == [(3, "paused"), (3, "stopping")]
    at.button(key="lt_ok_12").click().run()
    assert ("confirm", 12) in FakeService.calls


# The kill switch and square-off buttons need their confirm tick.
def test_kill_buttons_need_confirm(fakes):
    at = page()
    assert at.button(key="lt_kill").disabled and at.button(key="lt_square").disabled
    at.checkbox(key="lt_sure_kill").check().run()
    at.button(key="lt_kill").click().run()
    assert ("kill", False) in FakeService.calls
    at.checkbox(key="lt_sure_all").check().run()
    at.button(key="lt_square").click().run()
    assert ("kill", True) in FakeService.calls and fakes["all"] == ["stopped"]


# LIVE: red banner, kill switch state, and Deploy blocked while something is missing.
def test_live_mode_blocks_deploy(fakes, monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    fakes["kill"] = (True, "daily loss")
    at = page()
    html = " ".join(m.value for m in at.markdown)
    assert "LIVE: REAL MONEY" in html and "KILL SWITCH ON (daily loss)" in html
    assert at.button(key="lt_deploy").disabled
    assert any("Live deployment blocked" in e.value for e in at.error)
