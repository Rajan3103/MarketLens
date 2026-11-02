"""Tests for marketlens/trading/order_service.py: each risk rule, the kill switch and confirmations.

No real calls: Telegram, the Dhan login and the brokers are fakes, the clock is fixed.
The full-flow tests use TEST_DATABASE_URL (skipped when it is not set), never the real database.
"""
import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import select

from marketlens import config
from marketlens.broker.base import OrderResult
from marketlens.broker.candles import IST
from marketlens.broker.paper import PaperBroker
from marketlens.broker.session import LoginStatus
from marketlens.trading import order_service as os_
from marketlens.trading.order_service import Leg, OrderService

MONDAY_10AM = dt.datetime(2026, 11, 2, 10, 0, tzinfo=IST)
SATURDAY = dt.datetime(2026, 11, 7, 10, 0, tzinfo=IST)
CE = "NIFTY 25000 CE 03-Nov-2026"
PE = "NIFTY 25000 PE 03-Nov-2026"


# ====================================================================== each rule on its own (no database)

# Market hours: 09:15-15:30 on weekdays only.
def test_market_open_rule():
    assert os_.check_market_open(MONDAY_10AM) == ""
    assert "Market closed" in os_.check_market_open(MONDAY_10AM.replace(hour=9, minute=14))
    assert os_.check_market_open(MONDAY_10AM.replace(hour=15, minute=30)) == ""
    assert "Market closed" in os_.check_market_open(MONDAY_10AM.replace(hour=15, minute=31))
    assert "Market closed" in os_.check_market_open(SATURDAY)


# F&O quantities must be whole lots; shares (lot 1) never fail; an unknown lot size blocks.
def test_lot_size_rule():
    assert os_.check_lot(Leg(CE, "BUY", 130), 65) == ""
    assert "not a multiple of the lot size 65" in os_.check_lot(Leg(CE, "BUY", 100), 65)
    assert os_.check_lot(Leg("RELIANCE", "BUY", 7), 1) == ""
    assert "lot size unknown" in os_.check_lot(Leg(CE, "BUY", 65), None)
    assert os_.lot_size_for("RELIANCE") == 1 and os_.lot_size_for(CE) == 65
    assert os_.lot_size_for("BANKNIFTY-Nov2026-FUT") == 30


# Max capital per trade: qty x price; exits are never limited; no price = blocked.
def test_capital_rule():
    assert os_.check_capital(Leg("RELIANCE", "BUY", 10), 2500, 100_000) == ""
    assert "above MAX_CAPITAL_PER_TRADE_RS" in os_.check_capital(Leg("RELIANCE", "BUY", 50), 2500, 100_000)
    assert os_.check_capital(Leg("RELIANCE", "SELL", 50, opens=False), 2500, 100_000) == ""
    assert "no price" in os_.check_capital(Leg("RELIANCE", "BUY", 10), None, 100_000)
    assert os_.check_capital(Leg("RELIANCE", "BUY", 1000), 2500, 0) == ""          # 0 = no limit


# MAX_OPEN_POSITIONS: only new symbols count.
def test_open_positions_rule():
    held = {"A", "B", "C"}
    assert os_.check_open_positions({"D"}, held, 4) == ""
    assert "MAX_OPEN_POSITIONS (3)" in os_.check_open_positions({"D"}, held, 3)
    assert os_.check_open_positions({"A"}, held, 3) == ""                           # adding to an open one
    assert "2 more" in os_.check_open_positions({"D", "E"}, held, 4)


# Daily and strategy loss limits (0 = no limit).
def test_loss_rules():
    assert os_.daily_loss_hit(-2000, 2000) and not os_.daily_loss_hit(-1999.99, 2000)
    assert not os_.daily_loss_hit(-50_000, 0)
    assert "Kill switch ON" in os_.check_daily_loss(-2500, 2000)
    assert os_.check_strategy_loss(-999, 1000, "S") == ""
    assert "Strategy 'S' lost Rs 1,000" in os_.check_strategy_loss(-1000, 1000, "S")
    assert os_.strategy_loss_limit({"max_loss_rs": 300}) == 300
    assert os_.strategy_loss_limit({}) == config.MAX_STRATEGY_LOSS_RS
    assert "Kill switch is ON (test)" in os_.check_kill_switch(True, "test")
    assert os_.check_kill_switch(False) == ""


# Dhan login must last until 15:30 today.
def test_login_rule():
    good = LoginStatus("active", "ok", MONDAY_10AM + dt.timedelta(hours=20))
    short = LoginStatus("active", "ok", MONDAY_10AM.replace(hour=14))
    assert os_.check_login(good, MONDAY_10AM) == ""
    assert "not valid for today" in os_.check_login(short, MONDAY_10AM)
    assert "DH-901" in os_.check_login(LoginStatus("expired", "DH-901"), MONDAY_10AM)
    assert "not valid" in os_.check_login(None, MONDAY_10AM)


# Signal inputs are checked before anything is saved.
def test_make_legs_checks_inputs():
    legs = os_.make_legs("buy", symbol="RELIANCE", qty=10, price=2500)
    assert legs[0].side == "BUY" and legs[0].qty == 10
    two = os_.make_legs("SELL", legs=[{"symbol": CE, "qty": 65}, {"symbol": PE, "qty": 65, "side": "BUY"}])
    assert [l.side for l in two] == ["SELL", "BUY"]
    assert os_.make_legs("EXIT", symbol="RELIANCE")[0].qty is None
    for bad in (dict(action="HOLD", symbol="X", qty=1), dict(action="BUY", qty=1), dict(action="BUY", symbol="X"),
                dict(action="BUY", symbol="X", qty=-5), dict(action="BUY", legs=[])):
        with pytest.raises(ValueError):
            os_.make_legs(**bad)


# Held quantity and open symbols from a positions table (closed rows have qty 0).
def test_positions_helpers():
    pos = pd.DataFrame([{"symbol": "A", "qty": 10, "product": "CNC"}, {"symbol": "B", "qty": 0, "product": "INTRADAY"},
                        {"symbol": "C", "qty": -65, "product": "MARGIN"}])
    assert os_.held_qty(pos, Leg("A", "SELL")) == (10, "CNC")
    assert os_.held_qty(pos, Leg("B", "SELL")) == (0, None)
    assert os_.open_symbols(pos) == {"A", "C"}


# ====================================================================== the service (test database only)

class Clock:
    """A fixed, movable time."""
    def __init__(self, now=MONDAY_10AM):
        self.now = now

    def __call__(self):
        return self.now


class Recorder:
    """Stands in for Telegram: remembers every message and its buttons."""
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def __call__(self, text, buttons=None):
        self.sent.append((text, buttons))
        return self.ok


class CountingLimiter:
    """Stands in for the RateLimiter: counts how often an order waited for a slot."""
    def __init__(self):
        self.calls = 0

    def wait(self):
        self.calls += 1
        return 0.0


class FakeLive:
    """A live broker that only records what it was asked to send."""
    mode, master = "live", None

    def __init__(self, positions=None):
        self.sent, self.positions, self.exited = [], positions or [], False

    def get_positions(self):
        return pd.DataFrame(self.positions, columns=["symbol", "security_id", "product", "qty", "realized_pnl",
                                                     "unrealized_pnl"])

    def place_order(self, symbol, side, qty, order_type="MARKET", price=None, product="INTRADAY", tag=None,
                    security_id=None, trigger_price=None):
        self.sent.append((symbol, side, qty, order_type, tag))
        return OrderResult(f"DH{len(self.sent)}", "PLACED")

    def exit_all(self):
        self.exited = True
        return "Exit-all sent"


@pytest.fixture
def risk(monkeypatch):
    """Known risk settings for every service test (paper mode)."""
    for name, value in {"TRADING_MODE": "paper", "AUTO_TRADE": False, "MAX_DAILY_LOSS_RS": 2000.0,
                        "MAX_STRATEGY_LOSS_RS": 1000.0, "MAX_OPEN_POSITIONS": 3,
                        "MAX_CAPITAL_PER_TRADE_RS": 100_000.0, "SQUARE_OFF_ON_KILL": False,
                        "CONFIRM_TIMEOUT_MIN": 5}.items():
        monkeypatch.setattr(config, name, value)
    # live legs: no instrument list needed (Security ID = the symbol's length)
    import marketlens.broker.dhan_broker as db_
    monkeypatch.setattr(db_, "security_for", lambda symbol, security_id=None, master=None:
                        type("Sec", (), {"security_id": str(len(symbol)), "lot_size": 1})())
    monkeypatch.setattr(os_, "cached_login", lambda url=None: pytest.fail("the real Dhan login was called"))


@pytest.fixture
def make(db_url, db_session, risk):
    """Build an OrderService on the test database with fakes; pnl is set through the returned dict."""
    def build(broker=None, login=None, **kw):
        pnl = {"day": 0.0, "by": {}}
        service = OrderService(url=db_url, broker=broker or PaperBroker(save=False, capital=1_000_000),
                               now=Clock(), notify=Recorder(), limiter=CountingLimiter(),
                               login=login or (lambda: LoginStatus("active", "ok", MONDAY_10AM + dt.timedelta(hours=20))),
                               pnl=lambda mode, broker: (pnl["day"], pnl["by"]), **kw)
        service.pnl_state = pnl
        return service
    return build


# The rows of one table as dicts.
def rows(db_url, model):
    from marketlens.db import get_session
    with get_session(db_url) as session:
        return [{c.name: getattr(r, c.name) for c in model.__table__.columns}
                for r in session.scalars(select(model).order_by(model.id))]


# Paper mode: saved in signals, sent at once, order linked to the signal, message saved in notifications.
def test_paper_signal_is_sent_and_saved(make, db_url):
    from marketlens.models import Notification, Order, Signal
    s = make()
    result = s.submit_signal(None, "BUY", "RELIANCE", 10, price=2500, reason="EMA cross")
    assert result.status == "SENT" and result.orders[0].status == "FILLED"
    signal = rows(db_url, Signal)[0]
    assert signal["side"] == "BUY" and signal["reason"] == "EMA cross" and signal["params"]["mode"] == "paper"
    order = rows(db_url, Order)[0]
    assert order["signal_id"] == signal["id"] and order["status"] == "FILLED" and order["mode"] == "paper"
    assert "PAPER signal" in rows(db_url, Notification)[0]["message"]
    assert s.limiter.calls == 1


# Every leg waits for the rate limiter.
def test_every_leg_uses_the_rate_limiter(make):
    s = make()
    r = s.submit_signal(None, "SELL", legs=[{"symbol": CE, "qty": 65, "price": 120}, {"symbol": PE, "qty": 65, "price": 110}])
    assert r.status == "SENT" and s.limiter.calls == 2


# Market closed: blocked, orders saved as REJECTED with the reason, nothing reaches the broker.
def test_market_closed_blocks(make, db_url):
    from marketlens.models import Order
    s = make()
    s.now.now = SATURDAY
    r = s.submit_signal(None, "BUY", "RELIANCE", 10, price=2500)
    assert r.status == "BLOCKED" and "Market closed" in r.message
    assert s.broker.orders == [] and s.limiter.calls == 0
    order = rows(db_url, Order)[0]
    assert order["status"] == "REJECTED" and "Market closed" in order["error_message"]
    assert "blocked" in s.notify.sent[-1][0]


# Lot-size multiples.
def test_lot_rule_blocks(make):
    r = make().submit_signal(None, "BUY", CE, 100, price=120)
    assert r.status == "BLOCKED" and "multiple of the lot size 65" in r.message


# Max capital per trade.
def test_capital_rule_blocks(make):
    r = make().submit_signal(None, "BUY", "RELIANCE", 100, price=2500)
    assert r.status == "BLOCKED" and "MAX_CAPITAL_PER_TRADE_RS" in r.message


# MAX_OPEN_POSITIONS blocks a 4th symbol, but closing one is still allowed.
def test_open_positions_rule_blocks_entries_not_exits(make):
    s = make()
    for sym in ("A", "B", "C"):
        assert s.submit_signal(None, "BUY", sym, 1, price=100).status == "SENT"
    r = s.submit_signal(None, "BUY", "D", 1, price=100)
    assert r.status == "BLOCKED" and "MAX_OPEN_POSITIONS (3)" in r.message
    exit_ = s.submit_signal(None, "EXIT", "A", price=101)
    assert exit_.status == "SENT" and exit_.orders[0].filled_qty == 1
    assert s.submit_signal(None, "EXIT", "Z").status == "BLOCKED"           # nothing to exit


# A strategy's own loss limit blocks only that strategy's new orders.
def test_strategy_loss_limit(make, db_url):
    from marketlens.db import get_session
    from marketlens.models import Strategy
    with get_session(db_url) as session:
        session.add(Strategy(name="Straddle", kind="options", params={"max_loss_rs": 500}))
        session.commit()
    s = make()
    s.pnl_state["by"] = {1: -600.0}
    r = s.submit_signal("straddle", "BUY", "RELIANCE", 1, price=100)
    assert r.status == "BLOCKED" and "Strategy 'Straddle' lost Rs 600" in r.message
    assert s.submit_signal(None, "BUY", "RELIANCE", 1, price=100).status == "SENT"   # manual trades still fine
    with pytest.raises(ValueError, match="not in the Strategy Library"):
        s.submit_signal("nope", "BUY", "RELIANCE", 1, price=100)


# Daily loss limit: the kill switch turns on and blocks every new order for the rest of the day.
def test_daily_loss_trips_kill_switch(make, db_url):
    from marketlens.models import ErrorLog, KillSwitchEvent
    s = make()
    s.submit_signal(None, "BUY", "RELIANCE", 10, price=2500)
    s.pnl_state["day"] = -2000.0
    r = s.submit_signal(None, "BUY", "TCS", 1, price=4000)
    assert r.status == "BLOCKED" and "Daily loss limit hit" in r.message
    event = rows(db_url, KillSwitchEvent)[-1]
    assert event["active"] and not event["squared_off"] and "MAX_DAILY_LOSS_RS" in event["reason"]
    assert any("KILL SWITCH ON" in text for text, _ in s.notify.sent)
    assert any("Kill switch ON" in e["message"] for e in rows(db_url, ErrorLog))
    assert s.broker.open_count() == 1                                         # not squared off (not chosen)

    s.pnl_state["day"] = 0.0                                                  # even if P&L recovers ...
    again = s.submit_signal(None, "BUY", "TCS", 1, price=4000)
    assert again.status == "BLOCKED" and "Kill switch is ON" in again.message
    assert s.submit_signal(None, "EXIT", "RELIANCE", price=2400).status == "SENT"   # ... exits still allowed

    s.unkill()
    assert s.submit_signal(None, "BUY", "TCS", 1, price=4000).status == "SENT"


# The kill switch lasts for the day: the next trading day starts with it off.
def test_kill_switch_resets_next_day(make):
    s = make()
    s.kill("test")
    assert os_.kill_switch_status("paper", MONDAY_10AM, s.url)[0]
    assert not os_.kill_switch_status("paper", MONDAY_10AM + dt.timedelta(days=1), s.url)[0]
    assert not os_.kill_switch_status("live", MONDAY_10AM, s.url)[0]         # per mode


# SQUARE_OFF_ON_KILL=true: hitting the limit also closes every open position.
def test_kill_switch_square_off(make, db_url, monkeypatch):
    from marketlens.models import KillSwitchEvent
    monkeypatch.setattr(config, "SQUARE_OFF_ON_KILL", True)
    s = make()
    s.submit_signal(None, "BUY", "RELIANCE", 10, price=2500)
    s.submit_signal(None, "SELL", CE, 65, price=120)
    assert s.broker.open_count() == 2
    s.pnl_state["day"] = -5000.0
    assert s.submit_signal(None, "BUY", "TCS", 1, price=4000).status == "BLOCKED"
    assert s.broker.open_count() == 0
    assert rows(db_url, KillSwitchEvent)[-1]["squared_off"]


# Kill switch by hand in live mode uses Dhan's exit-all.
def test_manual_kill_live_exit_all(make, monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    live = FakeLive()
    s = make(broker=live)
    s.kill("panic", square_off=True)
    assert live.exited


# Live + AUTO_TRADE=false: saved as waiting, Telegram Confirm / Reject buttons, nothing sent yet.
def test_live_waits_for_confirmation_then_confirm(make, db_url, monkeypatch):
    from marketlens.models import Order
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    live = FakeLive()
    s = make(broker=live)
    r = s.submit_signal(None, "BUY", "RELIANCE", 10, price=2500)
    assert r.status == "WAITING_CONFIRMATION" and live.sent == []
    text, buttons = s.notify.sent[-1]
    assert buttons == [("✅ Confirm", f"confirm:{r.signal_id}"), ("❌ Reject", f"reject:{r.signal_id}")]
    assert rows(db_url, Order)[0]["status"] == "WAITING_CONFIRMATION"

    s.now.now += dt.timedelta(minutes=2)
    done = s.confirm_signal(r.signal_id)
    assert done.status == "SENT" and live.sent[0][:3] == ("RELIANCE", "BUY", 10)
    order = rows(db_url, Order)
    assert len(order) == 1 and order[0]["status"] == "PLACED" and order[0]["broker_order_id"] == "DH1"
    assert s.confirm_signal(r.signal_id).status == "ERROR"                    # cannot be sent twice


# Reject cancels; a late Confirm cancels too; risk rules run again at Confirm time.
def test_live_reject_timeout_and_recheck(make, db_url, monkeypatch):
    from marketlens.models import Order
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    live = FakeLive()
    s = make(broker=live)
    a = s.submit_signal(None, "BUY", "RELIANCE", 10, price=2500)
    assert s.reject_signal(a.signal_id).status == "CANCELLED"
    b = s.submit_signal(None, "BUY", "TCS", 5, price=4000)
    s.now.now += dt.timedelta(minutes=6)
    late = s.confirm_signal(b.signal_id)
    assert late.status == "CANCELLED" and "Confirmed after 6 minutes" in late.message
    c = s.submit_signal(None, "BUY", "INFY", 5, price=1500)
    s.kill("test")
    assert s.confirm_signal(c.signal_id).status == "BLOCKED"
    assert live.sent == []
    assert [o["status"] for o in rows(db_url, Order)] == ["CANCELLED", "CANCELLED", "REJECTED"]


# Live + AUTO_TRADE=true sends at once and keeps Dhan's order id.
def test_live_auto_trade_sends(make, db_url, monkeypatch):
    from marketlens.models import Order
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(config, "AUTO_TRADE", True)
    live = FakeLive()
    r = make(broker=live).submit_signal(None, "BUY", "RELIANCE", 10, price=2500)
    assert r.status == "SENT" and len(live.sent) == 1
    order = rows(db_url, Order)[0]
    assert order["mode"] == "live" and order["broker_order_id"] == "DH1" and order["signal_id"] == r.signal_id


# Live orders need today's Dhan login.
def test_live_needs_valid_login(make, monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    live = FakeLive()
    s = make(broker=live, login=lambda: LoginStatus("expired", "DH-901 token expired"))
    r = s.submit_signal(None, "BUY", "RELIANCE", 10, price=2500)
    assert r.status == "BLOCKED" and "Dhan login not valid" in r.message and live.sent == []


# A broker error is logged in error_log and the order saved as REJECTED; Telegram failures are recorded.
def test_errors_go_to_error_log(make, db_url):
    from marketlens.models import ErrorLog, Notification, Order

    class Broken(FakeLive):
        mode = "paper"

        def update_price(self, symbol, price):
            return []

        def place_order(self, *a, **k):
            raise RuntimeError("exchange down")
    s = make(broker=Broken())
    s.notify.ok = False
    r = s.submit_signal(None, "BUY", "RELIANCE", 10, price=2500)
    assert r.orders[0].status == "REJECTED"
    assert "exchange down" in rows(db_url, Order)[0]["error_message"]
    assert any("exchange down" in e["message"] for e in rows(db_url, ErrorLog))
    assert rows(db_url, Notification)[-1]["status"] == "failed"
