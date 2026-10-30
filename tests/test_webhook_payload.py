"""Tests for marketlens/webhook/payload.py: the MarketLens webhook format and its checks."""
import pytest

from marketlens import config
from marketlens.webhook import payload as wh

SECRET = "x" * 30


# A good TradingView message.
def message(**kw) -> dict:
    return {**{"secret": SECRET, "strategy": "EMA", "symbol": "reliance", "action": "buy", "qty": "10",
               "price": "2501.5", "note": "EMA cross"}, **kw}


def test_parse_tradingview():
    out = wh.parse_tradingview(message(), SECRET)
    assert (out["symbol"], out["action"], out["qty"], out["price"], out["note"]) == ("RELIANCE", "BUY", 10, 2501.5, "EMA cross")
    assert wh.parse_tradingview(message(action="EXIT"), SECRET)["action"] == "EXIT"
    # lots instead of qty: 2 lots of NIFTY = 2 x 65
    lots = wh.parse_tradingview(message(symbol="NIFTY 25000 CE 03-Nov-2026", qty=None, lots=2), SECRET)
    assert (lots["qty"], lots["lots"]) == (130, 2)
    # the older side + action shape still works
    assert wh.parse_tradingview(message(action="ENTRY", side="SELL"), SECRET)["action"] == "SELL"
    for bad, reason in (({"action": "HOLD"}, "action"), ({"symbol": "{{ticker}}"}, "symbol"), ({"qty": "0"}, "1 or more"),
                        ({"lots": "0", "qty": None}, "1 or more"), ({"price": "abc"}, "numbers"), ({"price": "-5"}, "price")):
        with pytest.raises(ValueError, match=reason):
            wh.parse_tradingview(message(**bad), SECRET)
    for bad in ({"secret": "wrong"}, {"secret": None}):
        with pytest.raises(wh.SecretError):                    # the server answers 401 for these
            wh.parse_tradingview(message(**bad), SECRET)
    with pytest.raises(wh.SecretError):
        wh.parse_tradingview(message(), "")                     # no secret configured = every alert rejected
    with pytest.raises(ValueError):
        wh.parse_tradingview(["not", "a", "dict"], SECRET)


def test_parse_chartink():
    signals = wh.parse_chartink(wh.CHARTINK_EXAMPLE, SECRET, SECRET)
    assert [(s["symbol"], s["price"], s["action"]) for s in signals] == [("RELIANCE", 2501.5, "BUY"), ("TCS", 3900.1, "BUY")]
    with pytest.raises(wh.SecretError):
        wh.parse_chartink(wh.CHARTINK_EXAMPLE, "nope", SECRET)
    with pytest.raises(ValueError, match="No stocks"):
        wh.parse_chartink({"stocks": ""}, SECRET, SECRET)


# localhost now, the AWS static IP later; the real secret is never in the addresses.
def test_urls(monkeypatch):
    monkeypatch.setattr(config, "SERVER_STATIC_IP", "")
    monkeypatch.setattr(config, "WEBHOOK_PORT", 8000)
    assert wh.webhook_urls()["tradingview"] == "http://localhost:8000/webhook/tradingview"
    monkeypatch.setattr(config, "SERVER_STATIC_IP", "13.200.1.2")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    urls = wh.webhook_urls()
    assert urls["tradingview"] == "http://13.200.1.2:8000/webhook/tradingview"
    assert urls["chartink"].endswith("?secret=" + wh.SECRET_PLACEHOLDER) and SECRET not in str(urls)


# The example message for TradingView uses placeholders only.
def test_tradingview_message(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    msg = wh.tradingview_message("EMA", action="SELL")
    assert msg["secret"] == wh.SECRET_PLACEHOLDER and msg["symbol"] == "{{ticker}}" and msg["action"] == "SELL"
    assert {"secret", "strategy", "symbol", "action", "qty", "price", "note"} <= set(msg)


# The last events come back newest first, with the secret hidden (test database).
def test_recent_events(db_url, db_session):
    from marketlens.models import WebhookEvent
    db_session.add_all([WebhookEvent(source="tradingview", payload=message(), processed=True),
                        WebhookEvent(source="chartink", payload={"stocks": "TCS"}, error="Wrong or missing secret")])
    db_session.commit()
    events = wh.recent_events(50, url=db_url)
    assert list(events["source"]) == ["chartink", "tradingview"]
    assert events["message"].iloc[1]["secret"] == "***" and SECRET not in str(events.to_dict())
