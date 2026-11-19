"""GET /api/market/status (market_clock.py) and GET /api/market/symbols (symbols.py).

Dates are worked out by hand: 19-Nov-2026 is a Thursday, 21-Nov a Saturday, 23-Nov a Monday and 24-Nov
(Guru Nanak Jayanti) an NSE holiday. No internet, no database, no keys.
"""
import datetime as dt
from functools import partial

import pytest
from fastapi.testclient import TestClient

from marketlens import config, market_clock
from marketlens.api import main as api_main
from marketlens.broker.candles import IST
from marketlens.market_clock import NSE_HOLIDAYS_PAGE, holiday_note, upcoming_holidays
from marketlens.symbols import MAX_MATCHES, all_symbols, search_symbols

HOLIDAYS = {dt.date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev", dt.date(2026, 12, 25): "Christmas"}


# A time in India.
def ist(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=IST)


# The clock at a fixed time with the small holiday list above.
def clock(*args) -> dict:
    return market_clock.market_clock(ist(*args), HOLIDAYS)


# The API with every key blank and DEMO mode on (the no-keys setup).
@pytest.fixture
def client(monkeypatch):
    for name in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "DHAN_API_KEY", "DHAN_API_SECRET", "DASHBOARD_PASSWORD"):
        monkeypatch.setattr(config, name, "")
    monkeypatch.setattr(config, "DEMO_MODE", True)
    return TestClient(api_main.create_app())


# ---------------------------------------------------------------- market clock

# Thursday 11:00: open until 15:30, next opening Friday 09:15.
def test_weekday_open():
    c = clock(2026, 11, 19, 11, 0)
    assert (c["state"], c["label"]) == ("open", "Open")
    assert c["closes_at"] == "2026-11-19T15:30:00+05:30" and c["next_open"] == "2026-11-20T09:15:00+05:30"
    assert c["now"] == "2026-11-19T11:00:00+05:30" and c["warning"] is None


# 09:10 is the pre-open session; it opens at 09:15 the same day. 08:00 is closed, opening the same day.
def test_pre_open_and_early_morning():
    c = clock(2026, 11, 19, 9, 10)
    assert (c["state"], c["label"], c["next_open"]) == ("pre_open", "Pre-open", "2026-11-19T09:15:00+05:30")
    early = clock(2026, 11, 19, 8, 0)
    assert early["state"] == "closed" and early["next_open"] == "2026-11-19T09:15:00+05:30"
    assert clock(2026, 11, 19, 9, 0)["state"] == "pre_open" and clock(2026, 11, 19, 9, 15)["state"] == "open"
    assert clock(2026, 11, 19, 15, 30)["state"] == "open" and clock(2026, 11, 19, 15, 31)["state"] == "closed"


# Saturday: closed (not a holiday), next opening Monday 09:15.
def test_weekend():
    c = clock(2026, 11, 21, 11, 0)
    assert (c["state"], c["reason"], c["next_open"]) == ("closed", "Saturday", "2026-11-23T09:15:00+05:30")


# Tuesday 24-Nov is a holiday; Monday evening's next opening skips it to Wednesday.
def test_holiday():
    c = clock(2026, 11, 24, 11, 0)
    assert (c["state"], c["label"]) == ("holiday", "Holiday") and "Guru Nanak" in c["reason"]
    assert c["next_open"] == "2026-11-25T09:15:00+05:30"
    assert clock(2026, 11, 23, 16, 0)["next_open"] == "2026-11-25T09:15:00+05:30"


# Past the last row of the list: a warning to update the file, with NSE's page; weekends are still known.
def test_date_past_the_list():
    c = clock(2027, 1, 4, 11, 0)                                    # a Monday in 2027
    assert c["state"] == "open" and c["list_ends"] == "2026-12-25"
    assert "Holiday list ends 25-Dec-2026, update data/nse_holidays.csv" in c["warning"]
    assert NSE_HOLIDAYS_PAGE in c["warning"]
    assert clock(2027, 1, 9, 11, 0)["state"] == "closed"           # Saturday
    # 31-Dec-2026 evening: today is covered, but the next opening (1-Jan-2027) is not
    assert "update data/nse_holidays.csv" in clock(2026, 12, 31, 16, 0)["warning"]


# No holiday file at all: weekends only, with a warning.
def test_no_holiday_list():
    c = market_clock.market_clock(ist(2026, 11, 24, 11, 0), {})
    assert c["state"] == "open" and c["list_ends"] is None and "No NSE holiday list" in c["warning"]


# The real file lists 2026; its last row is Christmas.
def test_real_holiday_file():
    from marketlens.scheduler.jobs import load_holidays
    holidays = load_holidays()
    assert max(holidays) == dt.date(2026, 12, 25) and all(d.year == 2026 for d in holidays)


# The Settings page note: the next three holidays.
def test_holiday_note():
    assert upcoming_holidays(dt.date(2026, 11, 20), HOLIDAYS) == sorted(HOLIDAYS.items())
    note = holiday_note(dt.date(2026, 11, 20), HOLIDAYS)
    assert "the 2 NSE holidays" in note and "(next: 24-Nov Prakash" in note and "25-Dec Christmas" in note


# The route answers the clock's dict (with a fixed time here).
def test_status_route(client, monkeypatch):
    real = market_clock.market_clock
    monkeypatch.setattr(market_clock, "market_clock", partial(real, ist(2026, 11, 19, 9, 10), HOLIDAYS))
    answer = client.get("/api/market/status")
    assert answer.status_code == 200
    body = answer.json()
    assert {k: body[k] for k in ("state", "label", "now", "next_open", "closes_at", "list_ends", "warning")} == {
        "state": "pre_open", "label": "Pre-open", "now": "2026-11-19T09:10:00+05:30",
        "next_open": "2026-11-19T09:15:00+05:30", "closes_at": None, "list_ends": "2026-12-25", "warning": None}
    assert "Pre-open session" in body["reason"]


# The real route (today's time): one of the four states and never an error.
def test_status_route_now(client):
    body = client.get("/api/market/status").json()
    assert body["label"] in ("Open", "Pre-open", "Closed", "Holiday") and body["now"].endswith("+05:30")


# ---------------------------------------------------------------- symbol search

# Every list is read: Nifty 50 / 200 stocks once each, ETFs and the app's indices.
def test_all_symbols():
    rows = {r["symbol"]: r for r in all_symbols()}
    assert rows["RELIANCE"]["lists"] == ["nifty50", "nifty200"] and rows["RELIANCE"]["kind"] == "stock"
    assert rows["NIFTYBEES"]["kind"] == "etf" and rows["NIFTYBEES"]["yahoo"] == "NIFTYBEES.NS"
    for ticker in ("^NSEI", "^NSEBANK", "^BSESN", "^INDIAVIX", "NIFTY_FIN_SERVICE.NS", "^CNXIT"):
        assert rows[ticker]["kind"] == "index"


# Best match first; indices keep the app's order (Nifty 50 first); at most 20.
def test_search_order():
    assert search_symbols("relia")[0]["symbol"] == "RELIANCE"
    assert search_symbols("reliance")[0]["symbol"] == "RELIANCE"                  # exact
    assert search_symbols("nifty")[0]["symbol"] == "^NSEI"
    assert search_symbols("^nsebank")[0]["symbol"] == "^NSEBANK"
    assert search_symbols("niftybees")[0]["symbol"] == "NIFTYBEES"
    assert len(search_symbols("a")) == MAX_MATCHES and len(search_symbols("a", 5)) == 5
    assert search_symbols("a", 99) == search_symbols("a") and search_symbols("   ") == []
    assert search_symbols("zzzzqqq") == []


# Ranking by hand on a tiny list: exact > starts with > a word starts with > anywhere.
def test_search_ranking_by_hand():
    rows = [{"symbol": "XBANKY", "yahoo": "XBANKY.NS", "name": "Something", "kind": "stock", "lists": []},
            {"symbol": "PQR", "yahoo": "PQR.NS", "name": "The Bank of Q", "kind": "stock", "lists": []},
            {"symbol": "BANKX", "yahoo": "BANKX.NS", "name": "Bank X", "kind": "stock", "lists": []},
            {"symbol": "BANK", "yahoo": "BANK.NS", "name": "Plain", "kind": "stock", "lists": []}]
    assert [r["symbol"] for r in search_symbols("bank", rows=rows)] == ["BANK", "BANKX", "PQR", "XBANKY"]


# The route: symbol, name and kind for each match; bad input is a friendly 400.
def test_symbols_route(client):
    body = client.get("/api/market/symbols", params={"q": "relia"}).json()
    assert body["query"] == "relia"
    assert body["matches"][0] == {"symbol": "RELIANCE", "yahoo": "RELIANCE.NS", "name": "Reliance Industries Ltd.",
                                  "kind": "stock", "lists": ["nifty50", "nifty200"]}
    assert len(client.get("/api/market/symbols", params={"q": "a"}).json()["matches"]) == 20
    assert len(client.get("/api/market/symbols", params={"q": "a", "limit": 3}).json()["matches"]) == 3
    for params in ({}, {"q": ""}, {"q": "a", "limit": 21}):
        bad = client.get("/api/market/symbols", params=params)
        assert bad.status_code == 400 and "error" in bad.json()


# No keys at all: both routes still work (they never need Dhan).
def test_no_keys(client):
    assert client.get("/api/market/status").status_code == 200
    assert client.get("/api/market/symbols", params={"q": "tcs"}).json()["matches"][0]["symbol"] == "TCS"
