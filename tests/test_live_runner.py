"""Tests for marketlens/trading/live_runner.py (module 11): deploying, and running deployed strategies.

The DEMO 9:20 straddle runs minute by minute with a fake clock on TEST_DATABASE_URL (never the real
database). Telegram, the Dhan login and the live broker are fakes: no real calls, no real orders.
"""
import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import select

from marketlens import config
from marketlens.backtest import options_bt as ob
from marketlens.broker.base import OrderResult
from marketlens.broker.candles import IST
from marketlens.broker.demo_data import DemoDataClient
from marketlens.broker.session import LoginStatus
from marketlens.trading import live_runner as lr
from marketlens.trading import order_service as os_

FWD_DAY = dt.date(2026, 10, 28)          # a Wednesday


class FakeClock:
    """Moves a DEMO client's clock and the runner's clock together."""
    def __init__(self, client, start):
        self.client, self.now = client, start
        client._now = start

    def __call__(self):
        return self.now

    def tick(self, minutes=1):
        self.now += dt.timedelta(minutes=minutes)
        self.client._now = self.now


class FakeLive:
    """A live broker that only records what it was asked to send."""
    mode, master = "live", None

    def __init__(self):
        self.sent = []

    def get_positions(self):
        return pd.DataFrame(columns=["symbol", "security_id", "product", "qty", "realized_pnl", "unrealized_pnl"])

    def place_order(self, symbol, side, qty, order_type="MARKET", price=None, product="INTRADAY", tag=None,
                    security_id=None, trigger_price=None):
        self.sent.append((symbol, side, qty))
        return OrderResult(f"DH{len(self.sent)}", "PLACED")


@pytest.fixture
def setup(db_url, db_session, monkeypatch, tmp_path):
    """Safe settings, a saved straddle (exit 10:00) and fakes for Telegram and Dhan symbols."""
    for name, value in {"TRADING_MODE": "paper", "AUTO_TRADE": False, "MAX_DAILY_LOSS_RS": 50_000.0,
                        "MAX_STRATEGY_LOSS_RS": 0.0, "MAX_OPEN_POSITIONS": 5, "MAX_CAPITAL_PER_TRADE_RS": 0.0,
                        "SQUARE_OFF_ON_KILL": False, "CONFIRM_TIMEOUT_MIN": 60}.items():
        monkeypatch.setattr(config, name, value)
    sent = []
    monkeypatch.setattr(os_, "_telegram", lambda text, buttons=None: sent.append(text) or True)
    monkeypatch.setattr(os_, "cached_login", lambda url=None: pytest.fail("the real Dhan login was called"))
    import marketlens.broker.dhan_broker as db_
    monkeypatch.setattr(db_, "security_for", lambda symbol, security_id=None, master=None:
                        type("Sec", (), {"security_id": "1", "lot_size": 65})())
    s = ob.template_settings("920_straddle", "NIFTY", FWD_DAY, FWD_DAY, exit_time=dt.time(10, 0))
    sid = ob.save_backtest_strategy("Live straddle", s, url=db_url)
    return {"sid": sid, "url": db_url, "tmp": tmp_path, "telegram": sent}


# A runner with DEMO prices and a fake clock starting at `start` on FWD_DAY.
def make_runner(setup, start=dt.time(9, 16, 3), service=None):
    client = DemoDataClient(spots={"NIFTY": 25000}, vix=14, moving=True)
    clock = FakeClock(client, dt.datetime.combine(FWD_DAY, start, IST))
    runner = lr.LiveRunner(setup["sid"], client=client, url=setup["url"], clock=clock, data_dir=setup["tmp"],
                           service=service(clock) if service else None)
    return runner, clock


# Run `minutes` cycles; returns the states seen.
def run(runner, clock, minutes):
    states = []
    for _ in range(minutes):
        states.append(runner.cycle()["state"])
        clock.tick()
    return states


# Rows of a table as dicts.
def rows(url, model, **where):
    from marketlens.db import get_session
    with get_session(url) as session:
        query = select(model).order_by(model.id)
        for k, v in where.items():
            query = query.where(getattr(model, k) == v)
        return [{c.name: getattr(r, c.name) for c in model.__table__.columns} for r in session.scalars(query)]


# Make a strategy ready for live: a saved backtest and `days` forward-test days (starting `first` days back).
def make_ready(url, sid, days=10, first=1, backtest=True):
    from marketlens.db import get_session
    from marketlens.models import BacktestRun, ForwardTestRun
    with get_session(url) as session:
        if backtest:
            session.add(BacktestRun(strategy_id=sid, period_start=FWD_DAY, period_end=FWD_DAY, status="done"))
            session.add(ForwardTestRun(strategy_id=sid, trade_date=FWD_DAY + dt.timedelta(days=30), status="stopped",
                                       state="closed", capital=500_000))     # a market-closed day does not count
        for i in range(days):
            session.add(ForwardTestRun(strategy_id=sid, trade_date=FWD_DAY - dt.timedelta(days=first + i),
                                       status="stopped", state="done", capital=500_000))
        session.commit()


# ---------------------------------------------------------------- deploying

# Live needs a saved backtest and 10 forward-test days; paper may deploy at once.
def test_deploy_rules(setup, monkeypatch):
    sid, url = setup["sid"], setup["url"]
    check = lr.deploy_check(sid, url)
    assert not check["ok_live"] and check["forward_days"] == 0 and len(check["missing"]) == 2
    assert "PAPER" in lr.deploy(sid, url=url)                                    # paper: allowed
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    with pytest.raises(ValueError, match="saved backtest.*10 more forward-test"):
        lr.deploy(sid, url=url)
    with pytest.raises(ValueError, match="Not ready"):
        lr.set_status(sid, "running", url)
    make_ready(url, sid, days=9)
    assert lr.deploy_check(sid, url)["missing"] == ["1 more forward-test day(s) on page 6 (9 of 10 done)"]
    make_ready(url, sid, days=1, first=10, backtest=False)
    assert lr.deploy_check(sid, url)["ok_live"]
    lr.deploy(sid, lots_multiplier=2, max_loss_rs=750, confirm=False, url=url)
    d = lr.get_deployment(sid, url)
    assert (d["mode"], d["status"], d["lots_multiplier"], d["confirm"], d["max_loss_rs"]) == ("live", "running", 2, False, 750)
    from marketlens.models import Strategy
    assert rows(url, Strategy, id=sid)[0]["params"]["max_loss_rs"] == 750           # the order service reads it
    with pytest.raises(ValueError, match="multiplier"):
        lr.deploy(sid, lots_multiplier=0, url=url)


# ---------------------------------------------------------------- running (paper)

# Paper: the same trades as the forward test, but every order went through the order service.
def test_paper_orders_go_through_order_service(setup):
    from marketlens.models import Order, Signal
    sid, url = setup["sid"], setup["url"]
    lr.deploy(sid, lots_multiplier=2, url=url)
    runner, clock = make_runner(setup)
    states = run(runner, clock, 46)                                              # 09:16 .. 10:01
    assert states[0] == "waiting" and "in_trade" in states and states[-1] == "done"
    orders = rows(url, Order, strategy_id=sid)
    signals = rows(url, Signal, strategy_id=sid)
    assert orders and len(signals) == len(orders) and all(o["signal_id"] for o in orders)
    assert {s["source"] for s in signals} == {"live"} and {o["status"] for o in orders} == {"FILLED"}
    assert {o["qty"] for o in orders} == {130}                                   # 1 lot x 65 x multiplier 2
    assert runner.broker.open_count() == 0
    dep = rows(url, __import__("marketlens.models", fromlist=["LiveDeployment"]).LiveDeployment)[0]
    assert dep["state"] == "done" and dep["heartbeat_at"] is not None and dep["params"]["trades"]


# Pause: no new orders; Stop: this strategy's positions are closed and it ends.
def test_pause_and_stop(setup):
    from marketlens.models import Order
    sid, url = setup["sid"], setup["url"]
    lr.deploy(sid, url=url)
    lr.set_status(sid, "paused", url)
    runner, clock = make_runner(setup)
    assert set(run(runner, clock, 6)) == {"paused"} and rows(url, Order) == []  # 09:20 entry skipped while paused
    lr.set_status(sid, "running", url)
    runner, clock = make_runner(setup)
    run(runner, clock, 6)
    assert runner.broker.open_count() == 2
    lr.set_status(sid, "stopping", url)
    assert runner.cycle()["stop"] and runner.broker.open_count() == 0
    assert lr.get_deployment(sid, url)["status"] == "stopped"


# A risk rule blocks the entry (capital per trade): nothing is sent, the exit is skipped too.
def test_risk_rules_apply(setup, monkeypatch):
    from marketlens.models import Order
    monkeypatch.setattr(config, "MAX_CAPITAL_PER_TRADE_RS", 100.0)
    sid, url = setup["sid"], setup["url"]
    lr.deploy(sid, url=url)
    runner, clock = make_runner(setup)
    run(runner, clock, 46)
    orders = rows(url, Order, strategy_id=sid)
    assert orders and {o["status"] for o in orders} == {"REJECTED"}
    assert all("MAX_CAPITAL_PER_TRADE_RS" in o["error_message"] for o in orders)
    assert runner.broker.open_count() == 0


# ---------------------------------------------------------------- running (live, fake Dhan)

# Build the live order service with a fake Dhan broker and a valid login.
def live_service(setup, live):
    return lambda clock: lr.RunnerService(url=setup["url"], broker=live, now=clock,
                                          notify=lambda text, buttons=None: True,
                                          login=lambda: LoginStatus("active", "ok",
                                                                    dt.datetime(2026, 10, 29, 9, 0, tzinfo=IST)))


# Live, confirm mode: entries wait; once confirmed they reach Dhan and the shadow copy; a restart rebuilds it.
def test_live_confirm_then_shadow(setup, monkeypatch):
    sid, url = setup["sid"], setup["url"]
    make_ready(url, sid)
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    lr.deploy(sid, url=url)                                                       # confirm=True
    live = FakeLive()
    runner, clock = make_runner(setup, service=live_service(setup, live))
    run(runner, clock, 5)                                                         # 09:16 .. 09:20: entry signals
    waiting = runner._params["waiting"]
    assert len(waiting) == 2 and live.sent == [] and runner.broker.open_count() == 0
    for signal_id in set(waiting.values()):
        assert runner.service.confirm_signal(signal_id).status == "SENT"
    assert len(live.sent) == 2
    run(runner, clock, 1)                                                         # next minute: sees the confirms
    assert runner.broker.open_count() == 2 and runner._params["waiting"] == {}
    again, _ = make_runner(setup, start=dt.time(9, 22, 3), service=live_service(setup, live))
    assert again.broker.open_count() == 2                                         # rebuilt from today's live orders


# Live, confirm mode: an exit that comes before you confirmed the entry cancels the entry, sends nothing.
def test_live_unconfirmed_entry_is_cancelled(setup, monkeypatch):
    from marketlens.models import Order
    sid, url = setup["sid"], setup["url"]
    make_ready(url, sid)
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    lr.deploy(sid, url=url)
    live = FakeLive()
    runner, clock = make_runner(setup, service=live_service(setup, live))
    run(runner, clock, 46)
    assert live.sent == [] and runner.broker.open_count() == 0
    statuses = {o["status"] for o in rows(url, Order, strategy_id=sid)}
    assert "CANCELLED" in statuses and "WAITING_CONFIRMATION" not in statuses


# Live, automatic (AUTO_TRADE=true and the deployment set to auto): sent at once.
def test_live_auto(setup, monkeypatch):
    sid, url = setup["sid"], setup["url"]
    make_ready(url, sid)
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(config, "AUTO_TRADE", True)
    lr.deploy(sid, confirm=False, url=url)
    live = FakeLive()
    runner, clock = make_runner(setup, service=live_service(setup, live))
    run(runner, clock, 6)
    assert len(live.sent) == 2 and runner.broker.open_count() == 2


# ---------------------------------------------------------------- the loop over all deployments

# One loop: heartbeat written; a paper deployment is paused when TRADING_MODE becomes live.
def test_run_once_heartbeat_and_mode_change(setup, monkeypatch):
    sid, url = setup["sid"], setup["url"]
    lr.deploy(sid, url=url)
    clock = lambda: dt.datetime.combine(FWD_DAY, dt.time(9, 18, 3), IST)  # noqa: E731
    made = []

    def maker(s):
        runner, _ = make_runner(setup, start=dt.time(9, 18, 3))
        made.append(runner)
        return runner
    runners = {}
    lines = lr.run_once(runners, url, clock, maker)
    assert made and sid in runners and "Live straddle" in lines[0]
    assert lr.heartbeats(url)[lr.RUNNER]["message"].startswith("paper mode, 1 active")
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    lr.run_once(runners, url, clock, maker)
    dep = lr.list_deployments(url).iloc[0]
    assert dep["status"] == "paused" and "deploy it again for LIVE" in dep["message"] and sid not in runners


# Kill switch with SQUARE_OFF_ON_KILL=true: everything is closed and every deployment stopped.
def test_kill_switch_square_off_stops_all(setup, monkeypatch):
    from marketlens.models import KillSwitchEvent
    sid, url = setup["sid"], setup["url"]
    lr.deploy(sid, url=url)
    runner, clock = make_runner(setup)
    run(runner, clock, 6)
    assert runner.broker.open_count() == 2
    os_.save_kill_switch(True, "test loss", "paper", clock(), url=url)
    assert "new orders blocked" in lr.handle_kill_switch("paper", clock(), url)  # not chosen: keep running
    monkeypatch.setattr(config, "SQUARE_OFF_ON_KILL", True)
    service = os_.OrderService(url=url, now=clock, notify=lambda text, buttons=None: True)
    assert "1 deployment(s) stopped" in lr.handle_kill_switch("paper", clock(), url, service=service)
    assert rows(url, KillSwitchEvent)[-1]["squared_off"]
    from marketlens.broker.paper import PaperBroker
    assert PaperBroker(url=url, strategy_id=sid).open_count() == 0
    assert lr.get_deployment(sid, url)["status"] == "stopped"


# The command line: one cycle with no deployments.
def test_command_line_once(setup, capsys):
    assert lr.main(["--once", "--at", "2026-10-28 09:18"], url=setup["url"]) == 0
    assert "no active deployments" in capsys.readouterr().out
