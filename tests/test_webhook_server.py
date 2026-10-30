"""Tests for marketlens/webhook/server.py with FastAPI's TestClient (test database only).

Telegram is replaced by a list that records the messages; the clock is a number the test moves.
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from marketlens import config
from marketlens.alerts import telegram
from marketlens.models import Signal, WebhookEvent
from marketlens.signals import rules as sr
from marketlens.webhook import payload as wh
from marketlens.webhook import server

SECRET = "test-secret-" + "x" * 20


class Clock:
    """A fake clock in seconds."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def client(db_url, db_session, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    sent, clock = [], Clock()
    app = server.create_app(url=db_url, clock=clock, notify=sent.append)
    c = TestClient(app)
    c.sent, c.clock, c.db = sent, clock, db_session
    return c


# A TradingView message (good secret unless changed).
def tv(**kw) -> dict:
    return {**{"secret": SECRET, "strategy": "EMA cross", "symbol": "RELIANCE", "action": "BUY", "qty": 10,
               "price": 2500, "note": "EMA 9 crossed above EMA 21"}, **kw}


# All rows of a table, oldest first.
def rows(c, model):
    c.db.expire_all()
    return c.db.scalars(select(model).order_by(model.id)).all()


def test_health(client):
    answer = client.get("/health")
    assert answer.status_code == 200 and answer.json()["status"] == "ok" and answer.json()["secret_set"] is True


def test_good_secret(client):
    answer = client.post("/webhook/tradingview", json=tv())
    assert answer.status_code == 200 and answer.json()["status"] == "ok"
    event, = rows(client, WebhookEvent)
    signal, = rows(client, Signal)
    assert event.processed and event.source == "tradingview" and event.payload["secret"] == "***"
    assert event.signal_id == signal.id and SECRET not in str(event.payload)
    assert (signal.side, signal.source, float(signal.price), signal.reason) == ("BUY", "webhook", 2500.0,
                                                                                "EMA 9 crossed above EMA 21")
    assert len(client.sent) == 1 and "BUY 10 RELIANCE at 2,500.00" in client.sent[0]
    assert "No strategy with this name" in client.sent[0]               # not in the library: saved only
    assert answer.json()["orders"] == [None]


@pytest.mark.parametrize("secret", ["wrong", None])
def test_bad_or_missing_secret(client, secret, monkeypatch):
    warnings = []
    monkeypatch.setattr(server.log, "warning", lambda *a: warnings.append(a))
    message = tv(secret=secret) if secret else {k: v for k, v in tv().items() if k != "secret"}
    answer = client.post("/webhook/tradingview", json=message)
    assert answer.status_code == 401 and answer.json()["status"] == "rejected"
    event, = rows(client, WebhookEvent)
    assert not event.processed and event.error.startswith("401") and not rows(client, Signal)
    assert client.sent == [] and warnings and "rejected" in warnings[0][0]


# The same alert again within 10 seconds is ignored; after 10 seconds it counts again.
def test_duplicate(client):
    assert client.post("/webhook/tradingview", json=tv()).json()["status"] == "ok"
    client.clock.now += 5
    second = client.post("/webhook/tradingview", json=tv())
    assert second.status_code == 200 and second.json() == {"status": "duplicate", "ignored": True}
    assert client.post("/webhook/tradingview", json=tv(price=2501)).json()["status"] == "ok"   # different alert
    client.clock.now += 11
    assert client.post("/webhook/tradingview", json=tv()).json()["status"] == "ok"
    events = rows(client, WebhookEvent)
    assert len(events) == 4 and [e.error for e in events].count("duplicate within 10 s (ignored)") == 1
    assert len(rows(client, Signal)) == 3 and len(client.sent) == 3


# Chartink: secret in the address, one signal per stock.
def test_chartink(client):
    answer = client.post(f"/webhook/chartink?secret={SECRET}", json=wh.CHARTINK_EXAMPLE)
    assert answer.status_code == 200 and len(answer.json()["signals"]) == 2
    signals = rows(client, Signal)
    assert [(s.side, float(s.price), s.params["symbol"]) for s in signals] == [("BUY", 2501.5, "RELIANCE"),
                                                                               ("BUY", 3900.1, "TCS")]
    event, = rows(client, WebhookEvent)
    assert event.source == "chartink" and event.payload["signal_ids"] == [s.id for s in signals]
    assert client.post("/webhook/chartink?secret=nope", json=wh.CHARTINK_EXAMPLE).status_code == 401
    assert client.post("/webhook/chartink", json=wh.CHARTINK_EXAMPLE).status_code == 401
    assert len(client.sent) == 2


# A strategy set to "auto" in the Strategy Library: BUY then EXIT become paper orders.
def test_auto_strategy_paper_orders(client, db_url):
    from marketlens.broker.paper import PaperBroker
    strategy = sr.SignalStrategy("RELIANCE", entry=[{"left": {"kind": "ema", "length": 9}, "op": "crosses_above",
                                                     "right": {"kind": "ema", "length": 21}}])
    sid = sr.save_signal_strategy("EMA cross", strategy, url=db_url, auto=True)
    buy = client.post("/webhook/tradingview", json=tv()).json()
    assert buy["orders"][0]["status"] == "FILLED" and buy["orders"][0]["price"] == 2500
    exit_ = client.post("/webhook/tradingview", json=tv(action="EXIT", price=2550)).json()
    assert exit_["orders"][0]["status"] == "FILLED"
    account = PaperBroker(strategy_id=sid, url=db_url, capital=1_000_000)
    assert account.get_funds()["realized_pnl"] == 500 and not account.positions        # (2550 - 2500) x 10
    assert "Paper order: FILLED" in client.sent[-1]
    nothing = client.post("/webhook/tradingview", json=tv(action="EXIT", price=2560)).json()
    assert nothing["orders"][0]["status"] == "REJECTED"                                  # nothing left to exit


# Live mode: no automatic orders (the live broker comes with module 11).
def test_live_mode_does_not_trade(client, db_url, monkeypatch):
    strategy = sr.SignalStrategy("RELIANCE", entry=[{"left": {"kind": "close"}, "op": ">",
                                                     "right": {"kind": "number", "value": 1}}])
    sr.save_signal_strategy("EMA cross", strategy, url=db_url, auto=True)
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(config, "AUTO_TRADE", False)
    assert client.post("/webhook/tradingview", json=tv()).json()["orders"][0]["status"] == "SKIPPED"
    monkeypatch.setattr(config, "AUTO_TRADE", True)
    order = client.post("/webhook/tradingview", json=tv(price=2600)).json()["orders"][0]
    assert order["status"] == "ERROR" and "not built yet" in order["message"]


# Messages that are not JSON or miss fields get 400 (and are still saved).
def test_bad_messages(client):
    assert client.post("/webhook/tradingview", content=b"BUY RELIANCE",
                       headers={"Content-Type": "text/plain"}).status_code == 400
    assert client.post("/webhook/tradingview", json=tv(action="HOLD")).status_code == 400
    assert client.post("/webhook/tradingview", json=tv(symbol="")).status_code == 400
    assert [e.error[:3] for e in rows(client, WebhookEvent)] == ["400", "400", "400"] and not rows(client, Signal)


# Telegram without keys: nothing is sent and nothing fails.
def test_telegram_without_keys(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    assert telegram.is_configured() is False and telegram.send_message("hello") is False
