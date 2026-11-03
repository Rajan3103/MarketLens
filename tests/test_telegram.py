"""Tests for marketlens/alerts/telegram.py and the places that send messages.

The network is never used: telegram._post is replaced (conftest.py blocks it for every test). Database
tests use TEST_DATABASE_URL.
"""
import datetime as dt
import urllib.error

import pytest

from marketlens import config, logger
from marketlens.alerts import telegram
from marketlens.trading import order_service as os_

TOKEN = "123456:SECRET-BOT-TOKEN"


# Telegram keys set and a fake Telegram that records what it got; answer = what it replies.
@pytest.fixture
def fake_telegram(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "987")
    got = {"messages": [], "answer": {"ok": True}}

    def post(message, timeout):
        got["messages"].append(message)
        if isinstance(got["answer"], Exception):
            raise got["answer"]
        return got["answer"]
    monkeypatch.setattr(telegram, "_post", post)
    return got


# The saved notifications, oldest first.
def saved(url):
    return list(reversed(telegram.recent_notifications(100, url)))


# ---------------------------------------------------------------- deliver

# Without keys nothing is sent: "skipped".
def test_skipped_without_keys():
    assert telegram.is_configured() is False
    assert telegram.deliver("hello") == ("skipped", "Telegram key not set (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")


# With keys: the chat, the text and the tap buttons go to Telegram.
def test_sent_with_buttons(fake_telegram):
    assert telegram.deliver("Confirm?", buttons=[("✅ Confirm", "confirm:7"), ("❌ Reject", "reject:7")]) == ("sent", None)
    message = fake_telegram["messages"][0]
    assert message["chat_id"] == "987" and message["text"] == "Confirm?"
    assert message["reply_markup"]["inline_keyboard"][0][1] == {"text": "❌ Reject", "callback_data": "reject:7"}
    telegram.deliver("x" * 5000)
    assert len(fake_telegram["messages"][1]["text"]) == telegram.MAX_LENGTH


# Telegram says no, or cannot be reached: "failed" with the reason, and the bot token is never shown.
def test_failed_and_token_hidden(fake_telegram):
    fake_telegram["answer"] = {"ok": False, "description": "Bad Request: chat not found"}
    assert telegram.deliver("x") == ("failed", "Telegram did not accept the message: Bad Request: chat not found")
    fake_telegram["answer"] = urllib.error.URLError(f"https://api.telegram.org/bot{TOKEN}/sendMessage timed out")
    status, error = telegram.deliver("x")
    assert status == "failed" and TOKEN not in error and "***" in error


# ---------------------------------------------------------------- saved in notifications

# Every message is saved: sent, failed or skipped, with its source and event.
def test_notify_saves_every_status(db_url, db_session, monkeypatch):
    assert telegram.notify("no keys", source="test", event="e1", url=db_url) is False
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "987")
    monkeypatch.setattr(telegram, "_post", lambda message, timeout: {"ok": True})
    assert telegram.send_message("works", source="test", event="e2", url=db_url) is True
    monkeypatch.setattr(telegram, "_post", lambda message, timeout: {"ok": False, "description": "blocked"})
    assert telegram.notify("refused", source="test", url=db_url) is False
    rows = saved(db_url)
    assert [(r["message"], r["status"], r["event"]) for r in rows] == [
        ("no keys", "skipped", "e1"), ("works", "sent", "e2"), ("refused", "failed", None)]
    assert rows[0]["error"].startswith("Telegram key not set") and rows[2]["error"].endswith("blocked")
    assert all(r["source"] == "test" for r in rows)


# No database: the message is still sent and nothing breaks.
def test_notify_without_database(fake_telegram):
    assert telegram.notify("hi", url="postgresql+psycopg://nobody@127.0.0.1:1/none") is True


# ---------------------------------------------------------------- hooks

# The order service's own messages carry their event; the real sender's "skipped" is kept as skipped.
@pytest.mark.parametrize("reason, statuses, event", [
    ("forward:X|1|09:20 (SL)", ["FILLED"], "sl_hit"), ("TRAIL_SL", ["FILLED"], "sl_hit"),
    ("M2M_SL", ["PLACED"], "sl_hit"), ("EMA cross", ["FILLED", "FILLED"], "order_filled"),
    ("EMA cross", ["PLACED"], "order_placed"), ("SL", ["REJECTED"], "order_rejected"),
    ("SLIPPAGE check", ["FILLED"], "order_filled"),
])
def test_order_events(reason, statuses, event):
    from marketlens.broker.base import OrderResult
    assert os_.order_event([OrderResult("1", s) for s in statuses], reason) == event


# Kill switch messages from the order service go to notifications as kill_switch, skipped without keys.
def test_order_service_kill_switch_is_saved(db_url, db_session, monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    service = os_.OrderService(url=db_url)                       # the real sender (no keys: skipped)
    service.kill("test")
    service.unkill("test over")
    rows = saved(db_url)
    kills = [r for r in rows if r["event"] == "kill_switch"]
    assert len(kills) == 2 and all(r["status"] == "skipped" and r["source"] == "order_service" for r in kills)
    assert "KILL SWITCH ON" in kills[0]["message"] and "Kill switch OFF" in kills[1]["message"]
    assert any(r["source"] == "error_log" for r in rows) is False   # a WARNING is not told


# An ERROR in the error log is told once (the same error again within 30 minutes is not); a WARNING is not.
def test_error_log_alerts(db_url, db_session):
    logger.record_error("marketlens.test", "Dhan refused the order", url=db_url)
    logger.record_error("marketlens.test", "Dhan refused the order", url=db_url)
    logger.record_error("marketlens.test", "only a warning", url=db_url, level="WARNING")
    rows = [r for r in saved(db_url) if r["source"] == "error_log"]
    assert len(rows) == 1 and rows[0]["event"] == "error" and "Dhan refused the order" in rows[0]["message"]
    assert logger.alert_due("m", "x", now=0) and not logger.alert_due("m", "x", now=1799)
    assert logger.alert_due("m", "x", now=1801)


# The webhook receiver's default sender saves its messages with source webhook.
def test_webhook_messages_are_saved(db_url, db_session):
    from marketlens.webhook.server import Receiver
    Receiver(url=db_url).notify("📩 TradingView alert: BUY RELIANCE")
    row = saved(db_url)[-1]
    assert (row["source"], row["event"], row["status"]) == ("webhook", "webhook_alert", "skipped")


# The forward tester tells about its paper orders (an SL exit as sl_hit).
def test_forward_test_orders_are_told(db_url, db_session):
    from marketlens.broker.base import OrderResult
    from marketlens.trading.forward import ForwardRunner
    runner = type("Runner", (), {"name": "9:20 straddle", "url": db_url})()
    ForwardRunner._tell_order(runner, "NIFTY 25000 CE 28-Oct-2026", "BUY", 65, OrderResult("1", "FILLED", avg_price=101.5),
                              "SL")
    ForwardRunner._tell_order(runner, "NIFTY 25000 PE 28-Oct-2026", "SELL", 65, OrderResult("2", "FILLED", avg_price=99),
                              "E|2|09:20")
    first, second = saved(db_url)[-2:]
    assert first["source"] == "forward_test" and first["event"] == "sl_hit" and "SL hit" in first["message"]
    assert "@ 101.50 - SL" in first["message"]
    assert second["event"] == "order_filled" and "E|2" not in second["message"]


# The live runner tells when it stops or pauses a strategy.
def test_live_runner_tells(db_url, db_session):
    from marketlens.trading import live_runner as lr
    lr._tell("⏹ Straddle stopped (PAPER)", "strategy_stopped", db_url)
    row = saved(db_url)[-1]
    assert (row["source"], row["event"]) == ("live_runner", "strategy_stopped")


# The 08:45 login reminder is saved with source login.
def test_login_reminder_is_saved(db_url, db_session, monkeypatch):
    from marketlens.broker import session as session_
    from marketlens.broker.candles import IST
    monkeypatch.setattr(session_, "check_login", lambda save=True, url=None, now=None: session_.LoginStatus(
        "expired", "Token expired", None))
    text = session_.remind_if_needed(now=dt.datetime(2026, 11, 3, 8, 45, tzinfo=IST), url=db_url)
    assert text and "Dhan login is not ready" in text
    row = saved(db_url)[-1]
    assert row["source"] == "login" and row["event"] == "login_reminder" and row["message"] == text
