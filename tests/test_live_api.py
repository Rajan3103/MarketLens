"""TestClient tests for marketlens/api/routes/live.py (module 11, Live Algo Trading & Bot Monitor).

Everything runs on TEST_DATABASE_URL (skipped when it is not set). Telegram, the Dhan login and the live
broker are fakes: no real calls and no real orders.
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from marketlens import config
from marketlens.api import main as api_main
from marketlens.api.routes import live as live_routes
from marketlens.broker.candles import IST
from marketlens.broker.session import LoginStatus
from marketlens.trading import live_runner as lr
from tests.test_live_runner import FWD_DAY, FakeLive, make_ready, setup  # noqa: F401  (setup is a fixture)

MARKET_TIME = dt.datetime.combine(FWD_DAY, dt.time(10, 0), IST)


@pytest.fixture
def client(setup, monkeypatch):
    monkeypatch.setattr(api_main, "record_error", lambda *a, **k: None)
    c = TestClient(api_main.create_app(url=setup["url"]))
    c.setup = setup
    return c


# A live order service with a fake Dhan, a valid login and a fixed time in market hours.
@pytest.fixture
def fake_live(client, monkeypatch):
    fake = FakeLive()
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(live_routes, "now", lambda: MARKET_TIME)
    monkeypatch.setattr(live_routes, "OrderService", lambda url=None: lr.RunnerService(
        url=url, broker=fake, now=lambda: MARKET_TIME, notify=lambda text, buttons=None: True,
        login=lambda: LoginStatus("active", "ok", dt.datetime(2026, 10, 29, 9, 0, tzinfo=IST))))
    return fake


# Banner and status cards: PAPER, AUTO_TRADE, kill switch, login, runner, API, Telegram, scheduler, database.
def test_status(client):
    body = client.get("/api/live/status").json()
    assert body["mode"] == "PAPER" and body["live"] is False and body["auto_trade"] is False
    assert body["kill_switch"] == {"on": False, "reason": ""} or body["kill_switch"]["on"] is False
    cards = body["cards"]
    assert set(cards) == {"login", "runner", "api", "telegram", "scheduler", "database"}
    assert cards["database"]["ok"] is True and cards["api"]["ok"] is True
    assert cards["login"]["ok"] is False and "Daily Dhan login" in cards["login"]["renew_steps"]
    assert cards["runner"]["state"] == "never" and cards["runner"]["alive"] is False
    assert "module 12" in cards["telegram"]["text"]
    lr.beat(lr.RUNNER, "paper mode", client.setup["url"])
    runner = client.get("/api/live/status").json()["cards"]["runner"]
    assert runner["state"] == "alive" and runner["ok"] is True


# The library list shows what each strategy needs; paper may deploy anyway, live may not.
def test_strategies_and_deploy_rules(client, monkeypatch):
    sid = client.setup["sid"]
    rows = client.get("/api/live/strategies").json()["strategies"]
    mine = next(r for r in rows if r["id"] == sid)
    assert mine["check"]["ok_live"] is False and mine["check"]["can_deploy"] is True
    assert any("saved backtest" in m for m in mine["check"]["missing"])
    ok = client.post("/api/live/deploy", json={"strategy_id": sid, "lots_multiplier": 2, "max_loss_rs": 3000,
                                                "confirm": True, "capital": 400_000})
    assert ok.status_code == 200 and "PAPER" in ok.json()["message"]
    assert ok.json()["deployment"]["lots_multiplier"] == 2 and ok.json()["deployment"]["capital"] == 400_000

    monkeypatch.setattr(config, "TRADING_MODE", "live")
    blocked = client.post("/api/live/deploy", json={"strategy_id": sid})
    assert blocked.status_code == 400 and "Not ready for live trading" in blocked.json()["error"]
    assert "forward-test day" in blocked.json()["error"]
    assert client.get(f"/api/live/strategies/{sid}/check").json()["can_deploy"] is False
    make_ready(client.setup["url"], sid)
    check = client.get(f"/api/live/strategies/{sid}/check").json()
    assert check["ok_live"] is True and check["forward_days"] == 10
    assert client.post("/api/live/deploy", json={"strategy_id": sid, "confirm": False}).status_code == 200
    bad = client.post("/api/live/deploy", json={"strategy_id": sid, "lots_multiplier": 0})
    assert bad.status_code == 400


# Start, pause and stop one deployment.
def test_start_pause_stop(client):
    sid = client.setup["sid"]
    client.post("/api/live/deploy", json={"strategy_id": sid})
    for status in ("paused", "running", "stopping"):
        answer = client.post(f"/api/live/deployments/{sid}/status", json={"status": status})
        assert answer.status_code == 200 and answer.json()["deployment"]["status"] == status
    deployments = client.get("/api/live/deployments").json()["deployments"]
    assert deployments[0]["status"] == "stopping" and deployments[0]["name"] == "Live straddle"
    assert client.post(f"/api/live/deployments/{sid}/status", json={"status": "flying"}).status_code == 400
    assert client.post("/api/live/deployments/9999/status", json={"status": "paused"}).status_code == 404


# Live, confirm mode: a waiting order shows on the monitor; Confirm sends it, Reject cancels the next one.
def test_monitor_confirm_and_reject(client, fake_live):
    sid = client.setup["sid"]
    service = live_routes.OrderService(client.setup["url"])
    first = service.submit_signal(sid, "BUY", "NIFTY-Nov2026-FUT", 65, reason="test entry", confirm=True)
    second = service.submit_signal(sid, "BUY", "NIFTY-Nov2026-FUT", 65, reason="second", confirm=True)
    assert first.status == second.status == "WAITING_CONFIRMATION"
    monitor = client.get("/api/live/monitor").json()
    assert [w["signal_id"] for w in monitor["waiting"]] == [first.signal_id, second.signal_id]
    assert monitor["waiting"][0]["reason"] == "test entry" and len(monitor["orders"]) == 2
    assert set(monitor) == {"running", "positions", "positions_note", "orders", "waiting"}

    sent = client.post(f"/api/live/signals/{first.signal_id}/confirm").json()
    assert sent["status"] == "SENT" and fake_live.sent == [("NIFTY-Nov2026-FUT", "BUY", 65)]
    rejected = client.post(f"/api/live/signals/{second.signal_id}/reject").json()
    assert rejected["status"] == "CANCELLED" and len(fake_live.sent) == 1
    again = client.post(f"/api/live/signals/{second.signal_id}/confirm")
    assert again.status_code == 404 and "waiting" in again.json()["error"]
    orders = client.get("/api/live/monitor").json()["orders"]
    assert {o["status"] for o in orders} == {"PLACED", "CANCELLED"} and orders[-1]["broker_order_id"] == "DH1"


# Kill switch and square off need the confirm word (the page sends it after the tick box and second click).
def test_kill_switch_and_square_off(client):
    sid = client.setup["sid"]
    client.post("/api/live/deploy", json={"strategy_id": sid})
    refused = client.post("/api/live/kill", json={})
    assert refused.status_code == 400 and "Tick the box" in refused.json()["error"]
    assert client.post("/api/live/kill", json={"confirm": "KILL"}).json()["on"] is True
    limits = client.get("/api/live/limits").json()
    assert limits["kill_switch"]["on"] is True and limits["daily"]["limit"] == 50_000
    assert client.get("/api/live/status").json()["kill_switch"]["on"] is True
    assert client.post("/api/live/unkill").json()["on"] is False
    assert client.get("/api/live/limits").json()["kill_switch"]["on"] is False

    assert client.post("/api/live/square-off", json={"confirm": "KILL"}).status_code == 400
    done = client.post("/api/live/square-off", json={"confirm": "SQUARE OFF"}).json()
    assert done["stopped"] == 1 and lr.get_deployment(sid, client.setup["url"])["status"] == "stopped"
    assert client.get("/api/live/limits").json()["kill_switch"]["on"] is True


# Daily and per-strategy loss limits come back with how much is used.
def test_limits(client, monkeypatch):
    import marketlens.trading.monitor as mon
    monkeypatch.setattr(mon, "strategy_pnl_today", lambda mode, now, url=None: {client.setup["sid"]: -1500.0})
    monkeypatch.setattr(config, "MAX_STRATEGY_LOSS_RS", 3000.0)
    body = client.get("/api/live/limits").json()
    assert body["daily"]["pnl"] == -1500 and body["daily"]["used"] == pytest.approx(0.03)
    row = body["strategies"][0]
    assert row["strategy"] == "Live straddle" and row["limit"] == 3000 and row["used"] == pytest.approx(0.5)


# The runner start button and the login check (both faked).
def test_runner_start_and_login_check(client, monkeypatch):
    monkeypatch.setattr(lr, "start_process", lambda: 4242)
    assert client.post("/api/live/runner/start").json()["pid"] == 4242
    lr.beat(lr.RUNNER, "running", client.setup["url"], now=dt.datetime.now(IST))
    monkeypatch.setattr(lr.fw, "pid_alive", lambda pid: True)
    assert client.post("/api/live/runner/start").json()["message"].startswith("The live runner is already running")
    import marketlens.broker.session as session_
    monkeypatch.setattr(session_, "check_login", lambda url=None: LoginStatus("expired", "Token expired", None))
    answer = client.post("/api/live/login/check").json()
    assert answer == {"status": "expired", "message": "Token expired", "ok": False}


# The error log with its filters.
def test_errors(client):
    from marketlens.logger import record_error
    url = client.setup["url"]
    record_error("marketlens.trading.live_runner", "strategy X failed: timeout", {"strategy_id": 1}, url=url)
    record_error("marketlens.api.main", "API call failed", url=url, level="WARNING")
    every = client.get("/api/live/errors").json()
    assert every["count"] == 2 and set(every["modules"]) >= {"marketlens.trading.live_runner", "marketlens.api.main"}
    only = client.get("/api/live/errors", params={"level": "ERROR"}).json()["errors"]
    assert [e["message"] for e in only] == ["strategy X failed: timeout"]
    by_text = client.get("/api/live/errors", params={"text": "API", "module": "marketlens.api.main"}).json()
    assert by_text["count"] == 1 and by_text["errors"][0]["level"] == "WARNING"
