"""Tests for the market data clients. They use the DEMO client only (no internet, no Dhan keys).

DemoDataClient(spots=..., vix=...) uses fixed numbers instead of yfinance, and a fixed
'now', so every run gives the same answers.
"""
import datetime as dt

import pandas as pd
import pytest

from marketlens import config
from marketlens.broker import dhan_data, market_data
from marketlens.broker.candles import COLUMNS, OPTION_COLUMNS
from marketlens.broker.demo_data import DemoDataClient, next_expiry
from marketlens.broker.dhan_client import DhanError
from marketlens.broker.dhan_data import (CHAIN_COLUMNS, DhanDataClient, parse_option_chain,
                                         parse_quotes, parse_strike)
from marketlens.broker.dhan_instruments import INDICES, Security
from marketlens.broker.market_data import get_data_client
from marketlens.broker.candles import IST

NOW = dt.datetime(2026, 10, 28, 11, 0, tzinfo=IST)     # a Wednesday
EXPIRY = dt.date(2026, 11, 3)                            # next NIFTY weekly expiry (Tuesday)


# A DEMO client with fixed spot prices and VIX.
@pytest.fixture
def demo():
    return DemoDataClient(spots={"NIFTY": 25000, "SENSEX": 82000, "BANKNIFTY": 56000, "RELIANCE": 1400},
                          vix=14, now=NOW)


# ---- choosing the client ----

# No keys + DEMO_MODE on -> DEMO client; DEMO_MODE off -> clear "key not set" error.
def test_get_data_client(monkeypatch):
    monkeypatch.setattr(dhan_data, "has_keys", lambda: False)
    monkeypatch.setattr(config, "DEMO_MODE", True)
    client = get_data_client()
    assert client.is_demo and client.source == "DEMO" and client.is_ready()
    monkeypatch.setattr(config, "DEMO_MODE", False)
    with pytest.raises(DhanError, match="key not set"):
        get_data_client()


# Without keys the Dhan client says it is not ready and refuses calls clearly.
def test_dhan_client_not_ready(monkeypatch):
    monkeypatch.setattr(dhan_data, "has_keys", lambda: False)
    client = DhanDataClient()
    assert not client.is_ready()
    with pytest.raises(DhanError, match="key not set"):
        client.get_expiry_list("NIFTY")


# Both clients offer the same methods, so pages can use either.
def test_same_methods():
    methods = ["is_ready", "get_quote", "get_expiry_list", "get_option_chain", "get_candles",
               "get_intraday", "get_expired_options", "get_margin", "get_holdings",
               "get_positions", "get_fund_limits"]
    for name in methods:
        assert callable(getattr(DhanDataClient, name)) and callable(getattr(DemoDataClient, name))


# ---- expiries ----

# NIFTY weekly on Tuesday, SENSEX weekly on Thursday, BANKNIFTY monthly (last Tuesday).
def test_expiry_list(demo):
    nifty = demo.get_expiry_list("NIFTY")
    assert nifty[0] == EXPIRY and all(d.weekday() == 1 for d in nifty)
    assert demo.get_expiry_list("SENSEX")[0] == dt.date(2026, 10, 29)
    bank = demo.get_expiry_list("BANKNIFTY")
    assert bank == [dt.date(2026, 11, 24), dt.date(2026, 12, 29), dt.date(2027, 1, 26)]


# The expiry an option belongs to, for any day.
def test_next_expiry():
    assert next_expiry("NIFTY", dt.date(2026, 10, 28), weekly=True) == dt.date(2026, 11, 3)
    assert next_expiry("NIFTY", dt.date(2026, 11, 3), weekly=True) == dt.date(2026, 11, 3)
    assert next_expiry("NIFTY", dt.date(2026, 10, 28), weekly=False) == dt.date(2026, 11, 24)


# ---- option chain ----

# A DEMO chain: right columns, strikes around the spot, sensible prices and Greeks.
def test_option_chain(demo):
    chain = demo.get_option_chain("NIFTY", EXPIRY)
    t = chain.table
    assert chain.source == "DEMO" and chain.spot == 25000 and list(t.columns) == CHAIN_COLUMNS
    assert len(t) == 41 and chain.atm_strike == 25000 and t["strike"].diff().dropna().eq(50).all()
    assert t["ce_ltp"].is_monotonic_decreasing and t["pe_ltp"].is_monotonic_increasing
    atm = t[t["strike"] == 25000].iloc[0]
    assert 0.45 < atm["ce_delta"] < 0.6 and atm["ce_theta"] < 0
    assert 50 < atm["ce_ltp"] < 400                    # about 1 week, VIX 14
    assert (t["ce_bid"] <= t["ce_ltp"]).all() and (t["ce_ask"] >= t["ce_ltp"]).all()
    assert (t[["ce_ltp", "pe_ltp"]] % 0.05).round(6).isin([0, 0.05]).all().all()   # 5-paise ticks


# Same inputs on the same day -> the same chain (pages don't jump around).
def test_chain_repeats(demo):
    a = demo.get_option_chain("NIFTY", EXPIRY).table
    b = demo.get_option_chain("NIFTY", EXPIRY).table
    pd.testing.assert_frame_equal(a, b)


# ---- candles ----

# DEMO minute bars: market hours, valid OHLC, labelled DEMO, start near the spot.
def test_intraday(demo):
    df = demo.get_intraday("NIFTY", dt.date(2026, 10, 26), dt.date(2026, 10, 27), interval=5)
    assert list(df.columns) == COLUMNS and len(df) == 2 * 75
    assert (df["source"] == "DEMO").all()
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert abs(df["close"].iloc[0] / 25000 - 1) < 0.02
    with pytest.raises(ValueError):
        demo.get_intraday("NIFTY", dt.date(2026, 10, 26), dt.date(2026, 10, 27), interval=2)


# DEMO option candles follow Black-Scholes: a deep in-the-money call is worth about spot - strike.
def test_option_candles(demo):
    deep_itm = Security("NIFTY-Nov2026-24000-CE", "DEMO", "NSE_FNO", "OPTIDX", "NSE", "option",
                        expiry=EXPIRY, lot_size=65, strike=24000, option_type="CE", underlying="NIFTY")
    df = demo.get_candles(deep_itm, dt.date(2026, 10, 27), dt.date(2026, 10, 27), 15)
    assert (df["close"] > 700).all() and df["oi"].notna().all()


# ---- expired options ----

# ATM+1 call: the strike follows the spot (one step above ATM); IV, OI and spot are filled in.
def test_expired_options(demo):
    df = demo.get_expired_options("NIFTY", "weekly", "ATM+1", "CE",
                                  dt.date(2026, 10, 19), dt.date(2026, 10, 23), interval=15)
    assert list(df.columns) == OPTION_COLUMNS and len(df) == 5 * 25
    atm = (df["spot"] / 50).round() * 50
    assert ((df["strike"] - atm) == 50).all()
    assert df["iv"].between(5, 60).all() and (df["source"] == "DEMO").all()


# Dhan's limits on expired-options requests are checked before asking.
@pytest.mark.parametrize("args, message", [
    (("NIFTY", "daily", "ATM", "CE"), "weekly"),
    (("NIFTY", "weekly", "ATM+11", "CE"), "ATM-10"),
    (("RELIANCE", "monthly", "ATM+4", "CE"), "ATM-3"),
    (("NIFTY", "weekly", "ATM", "XX"), "CE or PE"),
    (("NIFTY", "weekly", "OTM2", "CE"), "look like"),
])
def test_expired_options_checks(demo, args, message):
    with pytest.raises(ValueError, match=message):
        demo.get_expired_options(*args, dt.date(2026, 10, 19), dt.date(2026, 10, 23))


# "ATM", "ATM+2", "ATM-3" -> 0, 2, -3.
def test_parse_strike():
    assert [parse_strike(s, "NIFTY") for s in ("ATM", "atm+2", "ATM-3")] == [0, 2, -3]


# ---- quotes and account ----

# DEMO quotes come back as one row per symbol.
def test_quote(demo):
    q = demo.get_quote(["NIFTY", "RELIANCE"])
    assert list(q["symbol"]) == ["NIFTY", "RELIANCE"] and (q["source"] == "DEMO").all()
    assert q.loc[0, "ltp"] == 25000 and (q["low"] <= q["high"]).all()


# DEMO margin: selling 1 NIFTY lot ~12% of its value; buying the option = premium paid.
def test_margin(demo):
    opt = Security("NIFTY-Nov2026-25000-CE", "DEMO", "NSE_FNO", "OPTIDX", "NSE", "option",
                   expiry=EXPIRY, lot_size=65, strike=25000, option_type="CE", underlying="NIFTY")
    sell = demo.get_margin([{"security": opt, "side": "SELL", "qty": 65, "price": 200}])
    buy = demo.get_margin([{"security": opt, "side": "BUY", "qty": 65, "price": 200}])
    assert sell["total_margin"] == pytest.approx(0.12 * 25000 * 65)
    assert buy["total_margin"] == 200 * 65 and sell["source"] == "DEMO"


# DEMO account: no real holdings or positions, virtual money only.
def test_account(demo):
    assert demo.get_holdings().empty and demo.get_positions().empty
    funds = demo.get_fund_limits()
    assert funds["source"] == "DEMO" and funds["available_balance"] == 1_000_000


# ---- reading Dhan's answers (sample data, no client) ----

# Dhan's option chain answer becomes the same table shape as the DEMO chain.
def test_parse_option_chain():
    data = {"last_price": 25010.5, "oc": {
        "25050.000000": {"ce": {"last_price": 100, "oi": 500, "previous_oi": 400, "security_id": 42,
                                "greeks": {"delta": 0.45}, "implied_volatility": 12.1},
                         "pe": {"last_price": 140, "oi": 300, "previous_oi": 350, "security_id": 43}},
        "25000.000000": {"ce": {"last_price": 130, "oi": 800, "previous_oi": 800, "security_id": 40},
                         "pe": None}}}
    spot, t = parse_option_chain(data)
    assert spot == 25010.5 and list(t.columns) == CHAIN_COLUMNS
    assert list(t["strike"]) == [25000.0, 25050.0]
    row = t.iloc[1]
    assert row["ce_oi_change"] == 100 and row["pe_oi_change"] == -50 and row["ce_delta"] == 0.45
    assert row["ce_security_id"] == "42" and pd.isna(t.iloc[0]["pe_ltp"])


# Dhan's quote answer is labelled with our symbol names.
def test_parse_quotes():
    q = parse_quotes({"NSE_EQ": {"2885": {"last_price": 1400.5, "ohlc": {"open": 1390}, "volume": 9}}},
                     {("NSE_EQ", "2885"): "RELIANCE"})
    assert q.loc[0, "symbol"] == "RELIANCE" and q.loc[0, "ltp"] == 1400.5 and q.loc[0, "source"] == "DHAN"
