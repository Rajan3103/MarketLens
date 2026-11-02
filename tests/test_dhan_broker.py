"""Safety rules of the live Dhan broker, the broker factory and the Dhan login helper.

No real Dhan or Telegram call is ever made: dhanhq is replaced by FakeDhan and HTTP by fakes.
"""
import datetime as dt
import logging

import pandas as pd
import pytest

from marketlens import config
from marketlens.broker import dhan_broker as db
from marketlens.broker import session as ss
from marketlens.broker.candles import IST
from marketlens.broker.dhan_broker import DhanBroker, LiveTradingDisabled
from marketlens.broker.factory import get_broker
from marketlens.broker.paper import PaperBroker

TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0ZXN0LXRva2VuLWZha2UifQ.c2lnbmF0dXJlLXRoYXQtaXMtZmFrZQ"
EXPIRY = dt.date(2026, 11, 3)
MASTER = pd.DataFrame([
    {"exchange": "NSE", "segment_code": "E", "security_id": "2885", "instrument": "EQUITY", "symbol": "RELIANCE",
     "lot_size": 1, "display_name": "Reliance", "expiry": None, "series": "EQ", "name": "RELIANCE INDUSTRIES",
     "strike": None, "option_type": "", "underlying": "RELIANCE"},
    {"exchange": "NSE", "segment_code": "D", "security_id": "40001", "instrument": "OPTIDX",
     "symbol": "NIFTY-Nov2026-25000-CE", "lot_size": 65, "display_name": "NIFTY 03 NOV 25000 CALL",
     "expiry": EXPIRY, "series": "", "name": "", "strike": 25000.0, "option_type": "CE", "underlying": "NIFTY"},
])


class FakeDhan:
    """Stands in for dhanhq: records every call and answers like Dhan."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    # Any dhanhq method: record it and answer success (or the set answer).
    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if self.answer is not None:
                return self.answer
            data = {"orderId": "1001", "orderStatus": "TRANSIT"}
            if name in ("get_order_list", "get_trade_book", "get_positions", "get_holdings"):
                data = []
            if name == "get_order_by_id":
                data = {"orderType": "LIMIT", "quantity": 65, "price": 120, "triggerPrice": 0,
                        "securityId": "40001", "exchangeSegment": "NSE_FNO", "disclosedQuantity": 0}
            if name == "get_fund_limits":
                data = {"availabelBalance": 50_000, "utilizedAmount": 1_000}
            return {"status": "success", "remarks": "", "data": data}
        return call


@pytest.fixture
def live(monkeypatch):
    """Live mode with (fake) keys; returns a DhanBroker on FakeDhan that saves nothing."""
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(config, "DHAN_CLIENT_ID", "1100001234")
    monkeypatch.setattr(config, "DHAN_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(config, "DHAN_API_KEY", "")
    monkeypatch.setattr(config, "DHAN_API_SECRET", "")
    fake = FakeDhan()
    return DhanBroker(dhan=fake, master=MASTER, save=False), fake


# Every public method and arguments that would otherwise be valid.
CALLS = [("place_order", ("RELIANCE", "BUY", 1)), ("modify_order", ("1001",)), ("cancel_order", ("1001",)),
         ("place_super_order", ("RELIANCE", "BUY", 1, 100.0, 110.0, 95.0)), ("cancel_super_order", ("1001",)),
         ("exit_all", ()), ("get_orders", ()), ("get_trades", ()), ("get_positions", ()), ("get_holdings", ()),
         ("get_funds", ())]


# ---------------------------------------------------------------- live trading must be switched on

def test_paper_mode_refuses_to_build(monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "DHAN_CLIENT_ID", "1100001234")
    monkeypatch.setattr(config, "DHAN_ACCESS_TOKEN", TOKEN)
    fake = FakeDhan()
    with pytest.raises(LiveTradingDisabled, match="TRADING_MODE"):
        DhanBroker(dhan=fake, master=MASTER, save=False)
    assert fake.calls == []


def test_missing_keys_refuse_to_build(monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(config, "DHAN_ACCESS_TOKEN", "")
    with pytest.raises(LiveTradingDisabled, match="key not set"):
        DhanBroker(dhan=FakeDhan(), master=MASTER, save=False)


@pytest.mark.parametrize("method, args", CALLS)
def test_every_method_checks_again(live, monkeypatch, method, args):
    """Switching back to paper (or losing the keys) blocks every method before anything is sent."""
    broker, fake = live
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    with pytest.raises(LiveTradingDisabled):
        getattr(broker, method)(*args)
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(config, "DHAN_ACCESS_TOKEN", "")
    with pytest.raises(LiveTradingDisabled):
        getattr(broker, method)(*args)
    assert fake.calls == []


# ---------------------------------------------------------------- orders are checked before sending

def test_lots_rounded_down_never_up(live):
    broker, fake = live
    broker.place_order("NIFTY 25000 CE 03-Nov-2026", "SELL", 140, "LIMIT", price=120.5)
    name, _, kw = fake.calls[-1]
    assert name == "place_order" and kw["quantity"] == 130 and kw["security_id"] == "40001"
    assert kw["exchange_segment"] == "NSE_FNO" and kw["order_type"] == "LIMIT"
    with pytest.raises(ValueError, match="less than one lot"):
        broker.place_order("NIFTY-Nov2026-25000-CE", "BUY", 50)
    assert len(fake.calls) == 1


def test_order_types_and_refusals(live):
    broker, fake = live
    broker.place_order("RELIANCE", "SELL", 10, "SL-M", trigger_price=1300, product="CNC")
    kw = fake.calls[-1][2]
    assert kw["order_type"] == "STOP_LOSS_MARKET" and kw["trigger_price"] == 1300 and kw["product_type"] == "CNC"
    for args, kwargs, message in [
            (("NIFTY", "BUY", 65), {}, "index"),
            (("NOTLISTED", "BUY", 1), {}, "not in Dhan"),
            (("RELIANCE", "BUY", 1), {"security_id": "9999"}, "does not match"),
            (("NIFTY 25000 CE 03-Nov-2026", "BUY", 65), {"product": "CNC"}, "CNC"),
            (("RELIANCE", "BUY", 1, "LIMIT"), {}, "price"),
            (("RELIANCE", "BUY", 0), {}, "qty")]:
        with pytest.raises(ValueError, match=message):
            broker.place_order(*args, **kwargs)
    with pytest.raises(ValueError, match="target above"):
        broker.place_super_order("RELIANCE", "BUY", 1, price=100, target=95, stop_loss=90)
    assert len(fake.calls) == 1


def test_books_and_funds_are_mapped(live):
    broker, fake = live
    assert broker.get_funds()["available"] == 50_000
    assert broker.get_orders().empty and list(broker.get_positions().columns)[:4] == ["symbol", "security_id",
                                                                                      "product", "qty"]
    result = broker.modify_order("1001", price=118)
    name, args, _ = fake.calls[-1]
    assert name == "modify_order" and args[3] == 65 and args[4] == 118 and result.order_id == "1001"


def test_token_never_in_errors_or_logs(live, caplog):
    broker, fake = live
    fake.answer = {"status": "failure", "remarks": {"error_code": "DH-901", "error_message": f"bad token {TOKEN}"},
                   "data": ""}
    caplog.set_level(logging.DEBUG)
    with pytest.raises(Exception) as err:
        broker.place_order("RELIANCE", "BUY", 1)
    assert TOKEN not in str(err.value) and "***" in str(err.value) and "session renew" in str(err.value)
    assert TOKEN not in caplog.text
    assert db.scrub(f"x {TOKEN} y") == "x *** y"


# ---------------------------------------------------------------- the factory

def test_factory_paper_by_default(monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    assert isinstance(get_broker(save=False), PaperBroker)


def test_factory_live_needs_keys(monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(config, "DHAN_ACCESS_TOKEN", "")
    with pytest.raises(LiveTradingDisabled):
        get_broker(save=False)
    monkeypatch.setattr(config, "DHAN_CLIENT_ID", "1100001234")
    monkeypatch.setattr(config, "DHAN_ACCESS_TOKEN", TOKEN)
    broker = get_broker(dhan=FakeDhan(), master=MASTER, save=False)
    assert isinstance(broker, DhanBroker) and broker.mode == "live"


def test_webhook_orders_stay_paper_only(monkeypatch):
    from marketlens.trading.orders import broker_for
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    with pytest.raises(RuntimeError, match="no order was sent"):
        broker_for(1)


# ---------------------------------------------------------------- the login helper

@pytest.fixture
def keys(monkeypatch):
    """Fake keys, and HTTP that fails the test unless a test sets its own answer."""
    monkeypatch.setattr(config, "DHAN_CLIENT_ID", "1100001234")
    monkeypatch.setattr(config, "DHAN_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(config, "DHAN_API_KEY", "api-key-123456")
    monkeypatch.setattr(config, "DHAN_API_SECRET", "api-secret-123456")
    sent = []

    def no_http(method, url, headers=None, timeout=15):
        raise AssertionError(f"unexpected HTTP call to {url}")
    monkeypatch.setattr(ss, "http", no_http)
    monkeypatch.setattr(ss, "save_session", lambda result, url=None: sent.append(result.status))   # never the real DB
    return sent


NOW = dt.datetime(2026, 11, 2, 8, 45, tzinfo=IST)        # a Monday


# HTTP that answers with `data` and records what was asked.
def answering(monkeypatch, data, asked=None):
    def fake(method, url, headers=None, timeout=15):
        if asked is not None:
            asked.append((method, url, headers))
        return data
    monkeypatch.setattr(ss, "http", fake)


def test_check_login_states(keys, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    asked = []
    answering(monkeypatch, {"dhanClientId": "1100001234", "tokenValidity": "03/11/2026 09:00"}, asked)
    result = ss.check_login(save=False, now=NOW)
    assert result.status == "active" and result.expiry == dt.datetime(2026, 11, 3, 9, 0, tzinfo=IST)
    assert asked[0][:2] == ("GET", ss.PROFILE_URL) and result.valid_for_today(NOW)
    answering(monkeypatch, {"dhanClientId": "1100001234", "tokenValidity": "02/11/2026 08:00"})
    assert ss.check_login(save=False, now=NOW).status == "expired"
    answering(monkeypatch, {"dhanClientId": "999", "tokenValidity": "03/11/2026 09:00"})
    assert "different" in ss.check_login(save=False, now=NOW).message
    answering(monkeypatch, {"errorCode": "DH-901", "errorMessage": f"invalid {TOKEN}", "_http_status": 401})
    expired = ss.check_login(save=False, now=NOW)
    assert expired.status == "expired" and TOKEN not in expired.message
    assert TOKEN not in caplog.text


def test_check_login_without_keys_makes_no_call(keys, monkeypatch):
    monkeypatch.setattr(config, "DHAN_ACCESS_TOKEN", "")
    result = ss.check_login(save=False, now=NOW)                 # no_http would fail the test if called
    assert result.status == "failed" and "key not set" in result.message


def test_store_token_changes_only_its_line(keys, tmp_path, caplog):
    env = tmp_path / ".env"
    env.write_text("# my keys\nDHAN_CLIENT_ID=1100001234\nDHAN_ACCESS_TOKEN=old\nTRADING_MODE=paper\n", encoding="utf-8")
    caplog.set_level(logging.DEBUG)
    ss.store_token(TOKEN, None, env)
    assert env.read_text(encoding="utf-8") == ("# my keys\nDHAN_CLIENT_ID=1100001234\n"
                                               f"DHAN_ACCESS_TOKEN={TOKEN}\nTRADING_MODE=paper\n")
    assert TOKEN not in caplog.text and list(tmp_path.iterdir()) == [env]      # no temporary file left
    for bad in ("short", "has a space in it, so it is not a token"):
        with pytest.raises(ss.LoginError):
            ss.store_token(bad, None, env)
    with pytest.raises(ValueError):
        ss.set_env_value("DHAN_ACCESS_TOKEN", "two\nlines", env)


def test_bad_new_token_leaves_env_alone(keys, monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"DHAN_ACCESS_TOKEN={TOKEN}\n", encoding="utf-8")
    answering(monkeypatch, {"errorCode": "DH-901", "_http_status": 401})
    with pytest.raises(ss.LoginError, match="was not changed"):
        ss.renew_token("paste", path=env, token="x" * 40)
    assert env.read_text(encoding="utf-8") == f"DHAN_ACCESS_TOKEN={TOKEN}\n" and config.DHAN_ACCESS_TOKEN == TOKEN
    assert keys == ["expired"]                                  # the check was recorded by the stand-in only


def test_totp_and_consent_inputs_checked_before_any_call(keys):
    with pytest.raises(ss.LoginError, match="PIN"):
        ss.token_from_totp("12ab", "123456")
    with pytest.raises(ss.LoginError, match="6 digits"):
        ss.token_from_totp("123456", "12345")
    with pytest.raises(ss.LoginError, match="tokenId"):
        ss.consent_finish("   ")


def test_consent_flow_uses_documented_urls(keys, monkeypatch):
    asked = []
    answering(monkeypatch, {"consentAppId": "abc-123"}, asked)
    consent_id, login_url = ss.consent_start()
    assert consent_id == "abc-123" and login_url == "https://auth.dhan.co/login/consentApp-login?consentAppId=abc-123"
    method, url, headers = asked[0]
    assert method == "POST" and url == "https://auth.dhan.co/app/generate-consent?client_id=1100001234"
    assert headers == {"app_id": "api-key-123456", "app_secret": "api-secret-123456"}
    answering(monkeypatch, {"dhanClientId": "1100001234", "accessToken": TOKEN, "expiryTime": "2026-11-03T08:30:00"},
              asked)
    token, expiry = ss.consent_finish("https://myapp.example/callback?tokenId=tok-9")
    assert token == TOKEN and asked[-1][1] == "https://auth.dhan.co/app/consumeApp-consent?tokenId=tok-9"
    assert expiry == dt.datetime(2026, 11, 3, 8, 30, tzinfo=IST)


def test_reminder_rules(keys, monkeypatch):
    sent = []
    answering(monkeypatch, {"dhanClientId": "1100001234", "tokenValidity": "03/11/2026 08:00"})
    assert ss.remind_if_needed(NOW, sent.append) is None and sent == []           # valid all day
    answering(monkeypatch, {"dhanClientId": "1100001234", "tokenValidity": "02/11/2026 11:00"})
    text = ss.remind_if_needed(NOW, sent.append)                                   # expires mid-session
    assert "expires before the market closes" in text and sent == [text]
    answering(monkeypatch, {"errorCode": "DH-901", "errorMessage": f"x {TOKEN}", "_http_status": 401})
    text = ss.remind_if_needed(NOW, sent.append)
    assert "expired" in text and "session paste" in text and TOKEN not in text
    saturday = NOW + dt.timedelta(days=5)
    assert ss.remind_if_needed(saturday, sent.append) is None and len(sent) == 2


def test_next_reminder_skips_weekend():
    friday_evening = dt.datetime(2026, 11, 6, 18, 0, tzinfo=IST)
    assert ss.seconds_until_next(friday_evening) == (dt.datetime(2026, 11, 9, 8, 45, tzinfo=IST)
                                                     - friday_evening).total_seconds()
    assert ss.seconds_until_next(NOW - dt.timedelta(minutes=5)) == 300


def test_login_check_saved_once_a_day(db_url, db_session):
    from sqlalchemy import select

    from marketlens.models import BrokerSession
    ss.save_session(ss.LoginStatus("expired", "expired", None, NOW), db_url)
    ss.save_session(ss.LoginStatus("active", "Token valid", NOW + dt.timedelta(hours=20), NOW), db_url)
    rows = db_session.scalars(select(BrokerSession)).all()
    assert len(rows) == 1 and rows[0].status == "active" and rows[0].logged_in_at is not None
