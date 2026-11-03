"""Tests for the backend API (marketlens/api) with FastAPI's TestClient.

No internet (yfinance is replaced by made-up prices) and no database: the connection check and the
error_log writer are stand-ins, so nothing touches the real database.
"""
import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest
import yfinance
from fastapi.testclient import TestClient

import marketlens
from marketlens import config
from marketlens.api import main as api_main
from marketlens.api.schemas import df_to_records, fig_to_json, table_of
from marketlens.api.routes import market

SECRET_VALUE = "super-secret-dhan-token-123456"


@pytest.fixture
def client(monkeypatch):
    """The API app with a working (pretend) database and a recorder instead of error_log."""
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (True, "Connected to PostgreSQL 18"))
    errors = []
    monkeypatch.setattr(api_main, "record_error", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DHAN_CLIENT_ID", "")
    market.CACHE.clear()                                    # no answers left over from another test
    monkeypatch.setitem(market._refresh, "job_id", None)
    c = TestClient(api_main.create_app())
    c.errors = errors
    return c


# 100 made-up trading days: falling then rising, so MA20 crosses MA50 upwards once.
def fake_prices(days=100) -> pd.DataFrame:
    dates = pd.bdate_range("2026-03-02", periods=days)
    close = np.concatenate([np.linspace(200, 150, 60), np.linspace(151, 260, days - 60)])
    df = pd.DataFrame({"Open": close - 1, "High": close + 2, "Low": close - 2, "Close": close,
                       "Volume": np.arange(days) * 1000 + 5000}, index=dates)
    df.columns = pd.MultiIndex.from_product([df.columns, ["RELIANCE.NS"]])      # like yfinance answers
    return df


# ---------------------------------------------------------------- health and settings

def test_health(client, monkeypatch):
    answer = client.get("/api/health")
    assert answer.status_code == 200
    assert answer.json() == {"status": "ok", "version": marketlens.__version__, "database": "ok", "mode": "PAPER",
                             "demo": True}
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (False, "not running"))
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    body = client.get("/api/health").json()
    assert body["database"] == "fail" and body["mode"] == "LIVE"


def test_settings_status_never_shows_keys(client, monkeypatch):
    monkeypatch.setattr(config, "DHAN_ACCESS_TOKEN", SECRET_VALUE)
    answer = client.get("/api/settings/status")
    body = answer.json()
    assert answer.status_code == 200 and SECRET_VALUE not in answer.text
    keys = {k["name"]: k["set"] for k in body["keys"]}
    assert keys["DHAN_ACCESS_TOKEN"] is True and keys["DHAN_CLIENT_ID"] is False
    assert body["trading_mode"] == "PAPER" and body["database"]["status"] == "ok"


# ---------------------------------------------------------------- market history

def test_market_history(client, monkeypatch):
    asked = {}
    monkeypatch.setattr(yfinance, "download", lambda ticker, **kw: asked.update(ticker=ticker, **kw) or fake_prices())
    answer = client.get("/api/market/history", params={"symbol": "reliance.ns", "period": "6MO"})
    assert answer.status_code == 200
    body = answer.json()
    assert asked["ticker"] == "RELIANCE.NS" and asked["period"] == "2y"          # extra history for the averages
    assert body["count"] == 100 and "not a live trading recommendation" in body["note"]
    names = [t.get("name") for t in body["chart"]["data"]]
    assert names == ["Price", "20-day MA", "50-day MA", "Buy (bullish cross)", "Sell (bearish cross)", "RSI 14",
                     "Volume"]
    assert len(body["chart"]["data"][3]["x"]) == 1 and body["chart"]["data"][4]["x"] == []
    assert body["fast"] == 20 and body["slow"] == 50 and body["no_volume"] is False
    first, last = body["rows"][0], body["rows"][-1]
    assert first["date"] == "2026-03-02" and first["ma_fast"] is None and first["ma_slow"] is None  # NaN -> null
    assert set(first) == {"date", "open", "high", "low", "close", "volume", "ma_fast", "ma_slow", "ma200", "rsi",
                          "bb_upper", "bb_lower", "signal"}
    closes = [r["close"] for r in body["rows"]]
    assert last["ma_fast"] == pytest.approx(sum(closes[-20:]) / 20, abs=0.01)
    assert last["ma_slow"] == pytest.approx(sum(closes[-50:]) / 50, abs=0.01)
    assert last["ma200"] is None and 0 <= last["rsi"] <= 100 and last["bb_lower"] < last["bb_upper"]
    assert all(round(r["close"], 2) == r["close"] for r in body["rows"])                     # rounded to paise
    assert [s["type"] for s in body["signals"]] == ["bullish"]
    cross = next(r for r in body["rows"] if r["date"] == body["signals"][0]["date"])
    assert cross["signal"] == 1 and cross["ma_fast"] > cross["ma_slow"]


# Other averages, the 200-day MA and Bollinger Bands are drawn only when asked; fast must be below slow.
def test_market_history_options(client, monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda ticker, **kw: fake_prices())
    body = client.get("/api/market/history", params={"symbol": "RELIANCE.NS", "fast": 10, "slow": 30,
                                                      "ma200": "true", "bollinger": "true"}).json()
    names = [t.get("name") for t in body["chart"]["data"]]
    assert names[:5] == ["Price", "10-day MA", "30-day MA", "200-day MA", "Bollinger upper"]
    assert "Bollinger lower" in names and body["fast"] == 10
    closes = [r["close"] for r in body["rows"]]
    assert body["rows"][-1]["ma_fast"] == pytest.approx(sum(closes[-10:]) / 10, abs=0.01)
    wrong = client.get("/api/market/history", params={"symbol": "RELIANCE.NS", "fast": 50, "slow": 20})
    assert wrong.status_code == 400 and "must be shorter" in wrong.json()["error"]
    too_long = client.get("/api/market/history", params={"symbol": "RELIANCE.NS", "slow": 400})
    assert too_long.status_code == 400


# The price download is kept for 60 seconds, so changing the averages does not download again.
def test_market_history_is_cached(client, monkeypatch):
    calls = []
    monkeypatch.setattr(yfinance, "download", lambda ticker, **kw: calls.append(ticker) or fake_prices())
    for fast in (10, 15, 20):
        assert client.get("/api/market/history", params={"symbol": "RELIANCE.NS", "fast": fast}).status_code == 200
    assert calls == ["RELIANCE.NS"]


def test_market_history_friendly_errors(client, monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda ticker, **kw: pd.DataFrame())
    bad_period = client.get("/api/market/history", params={"symbol": "RELIANCE.NS", "period": "7y"})
    assert bad_period.status_code == 400 and bad_period.json() == {
        "error": "period must be one of 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max (not '7y')"}
    unknown = client.get("/api/market/history", params={"symbol": "NOPE.NS"})
    assert unknown.status_code == 400 and "No prices found for NOPE.NS" in unknown.json()["error"]
    missing = client.get("/api/market/history")
    assert missing.status_code == 400 and missing.json()["error"].startswith("Please check the request: symbol")


# An unexpected error: 500 with a plain message, no traceback; the details are logged.
def test_unexpected_error_is_hidden(client, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("database password=hunter2 at line 42")
    monkeypatch.setattr(market, "download_history", broken)
    answer = client.get("/api/market/history", params={"symbol": "RELIANCE.NS"})
    assert answer.status_code == 500 and set(answer.json()) == {"error"}
    assert "hunter2" not in answer.text and "Traceback" not in answer.text
    assert client.errors and "RuntimeError" in client.errors[0][1]
    assert client.get("/no/such/page").json() == {"error": "No such address. See /docs for the list."}


# Only the chosen period is returned; the extra downloaded days only warm up the averages.
def test_market_history_trims_to_period(client, monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda ticker, **kw: fake_prices(300))
    rows = client.get("/api/market/history", params={"symbol": "RELIANCE.NS", "period": "6mo"}).json()["rows"]
    assert rows[-1]["date"] == "2027-04-23" and rows[0]["date"] >= "2026-10-23"
    assert rows[0]["ma_slow"] is not None                                      # ready on the first day shown


# The download: a CSV or an Excel file with a file name.
def test_market_history_download(client, monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda ticker, **kw: fake_prices())
    csv = client.get("/api/market/history/download", params={"symbol": "RELIANCE.NS", "period": "1y"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert 'filename="RELIANCE.NS_1y_prices.csv"' in csv.headers["content-disposition"]
    lines = csv.text.strip().splitlines()
    assert lines[0] == "date,open,high,low,close,volume,ma20,ma50,ma200,rsi14,bb_upper,bb_lower,signal"
    assert len(lines) == 101
    slow200 = client.get("/api/market/history/download", params={"symbol": "RELIANCE.NS", "fast": 50, "slow": 200})
    assert slow200.text.splitlines()[0] == "date,open,high,low,close,volume,ma50,ma200,rsi14,bb_upper,bb_lower,signal"
    xlsx = client.get("/api/market/history/download", params={"symbol": "^NSEI", "format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"                # an .xlsx is a zip file
    assert 'filename="NSEI_1y_prices.xlsx"' in xlsx.headers["content-disposition"]
    bad = client.get("/api/market/history/download", params={"symbol": "X", "format": "pdf"})
    assert bad.status_code == 400 and bad.json() == {"error": "format must be csv or xlsx"}


# ---------------------------------------------------------------- one server: API + webhook

def test_webhook_router_is_included(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {"/api/health", "/api/settings/status", "/api/market/history", "/api/market/history/download",
            "/api/market/snapshot", "/api/market/sectors",
            "/api/jobs/{job_id}", "/api/jobs/{job_id}/cancel", "/webhook/tradingview",
            "/webhook/chartink", "/webhook/health"} <= paths
    assert client.get("/webhook/health").json()["status"] == "ok"
    assert client.get("/health").json()["status"] == "ok"                     # the old address still works


# Both commands start the same app with uvicorn (nothing is really started here).
def test_launchers(monkeypatch):
    import uvicorn

    from marketlens.api import __main__ as launcher
    from marketlens.webhook import server
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    monkeypatch.setattr(config, "SERVER_STATIC_IP", "")
    monkeypatch.setattr(config, "API_PORT", 8123)
    launcher.main([])
    launcher.main(["--no-reload", "--port", "9000"])
    server.main([])
    assert calls[0] == ("marketlens.api.main:app", {"host": "127.0.0.1", "port": 8123, "reload": True,
                                                    "reload_dirs": ["marketlens"], "log_level": "info"})
    assert calls[1][1]["reload"] is False and calls[1][1]["port"] == 9000
    assert calls[2][0] == "marketlens.api.main:app" and calls[2][1]["port"] == 8123


# ---------------------------------------------------------------- JSON helpers

def test_df_to_records_and_table():
    df = pd.DataFrame({"when": [pd.Timestamp("2026-11-03 09:15", tz="Asia/Kolkata"), pd.NaT],
                       "day": [dt.date(2026, 11, 3), None], "x": [np.float64(1.5), np.nan],
                       "n": np.array([1, 2], dtype=np.int64), "ok": [np.bool_(True), False], "inf": [np.inf, 2.0]})
    rows = df_to_records(df)
    assert rows[0] == {"when": "2026-11-03T09:15:00+05:30", "day": "2026-11-03", "x": 1.5, "n": 1, "ok": True,
                       "inf": None}
    assert rows[1]["when"] is None and rows[1]["x"] is None and rows[1]["day"] is None
    assert type(rows[0]["n"]) is int
    dated = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2026-11-03"]))
    assert df_to_records(dated) == [{"date": "2026-11-03T00:00:00", "close": 1.0}]
    table = table_of(df)
    assert table.columns == list(df.columns) and len(table.rows) == 2
    assert df_to_records(pd.DataFrame()) == []


def test_fig_to_json():
    fig = go.Figure(go.Scatter(x=pd.to_datetime(["2026-11-02", "2026-11-03"]), y=np.array([1.0, 2.5]), name="Close"))
    fig.update_layout(title="RELIANCE")
    out = fig_to_json(fig)
    assert out["data"][0]["name"] == "Close" and out["data"][0]["y"] == [1.0, 2.5]
    assert out["layout"]["title"]["text"] == "RELIANCE"
    assert go.Figure(out).data[0].name == "Close"                             # the browser gets the same chart


# ---------------------------------------------------------------- snapshot and sectors

# A made-up snapshot: 5 index cards (VIX up 5 %) and 3 stocks.
def fake_snapshot():
    from marketlens.overview import INDEX_CARDS, SOURCE_YF, Snapshot, _table, add_changes
    idx = [(sym, card, 105.0 if sym == "INDIAVIX" else 101.123, 100.0) for card, sym, _ in INDEX_CARDS]
    stocks = [("AAA", "Aaa", 103.0, 100.0), ("BBB", "Bbb", 98.0, 100.0), ("CCC", "Ccc", 100.0, 100.0)]
    return Snapshot(_table(idx), add_changes(_table(stocks)), SOURCE_YF, dt.datetime(2026, 11, 3, 10, 30))


class FakeJob:
    """Stands in for JobContext: remembers the progress messages."""
    def __init__(self):
        self.messages = []

    def progress(self, percent, message=None):
        self.messages.append(message)


# Jobs without a database: start_job runs the work at once; get_job says whether it is still running.
@pytest.fixture
def jobs(monkeypatch):
    state = {"started": [], "running": False}

    def start_job(kind, work, params=None):
        state["started"].append(kind)
        if not state["running"]:
            work(FakeJob())
        return f"job{len(state['started'])}"
    monkeypatch.setattr(market, "start_job", start_job)
    monkeypatch.setattr(market, "get_job", lambda job_id: {"job_id": job_id, "finished": not state["running"]})
    monkeypatch.setattr(market, "get_snapshot", fake_snapshot)
    return state


# The first call starts a job; once it has finished the snapshot is served from memory for 55 seconds.
def test_snapshot_runs_as_a_job(client, jobs):
    first = client.get("/api/market/snapshot").json()
    assert first["job_id"] == "job1" and jobs["started"] == ["market_snapshot"]
    snap = first["snapshot"]
    assert [c["name"] for c in snap["indices"]] == ["Nifty 50", "Bank Nifty", "Fin Nifty", "Sensex", "India VIX"]
    assert snap["indices"][0]["last"] == 101.12 and snap["indices"][0]["pct_change"] == pytest.approx(1.12)
    assert snap["indices"][-1]["inverse"] is True and snap["indices"][0]["inverse"] is False
    assert [g["symbol"] for g in snap["gainers"]] == ["AAA"] and [x["symbol"] for x in snap["losers"]] == ["BBB"]
    assert snap["breadth"] == {"advances": 1, "declines": 1, "unchanged": 1, "ad_ratio": 1.0}
    assert len(snap["breadth_chart"]["data"]) == 3 and snap["stock_count"] == 3
    assert snap["live"] is False and "Dhan keys" in snap["key_note"] and snap["as_of_text"].endswith("IST")
    assert isinstance(first["open"], bool) and first["status"]

    second = client.get("/api/market/snapshot").json()
    assert second["job_id"] is None and second["snapshot"] == snap and jobs["started"] == ["market_snapshot"]


# A stale snapshot is shown at once while a new job fetches; a running job is reused, not started twice.
def test_snapshot_stale_and_running_job(client, jobs):
    market.CACHE.put("snapshot", {"old": True})
    market.CACHE._items["snapshot"] = (market.CACHE._clock() - 120, {"old": True})       # 2 minutes old
    jobs["running"] = True
    first = client.get("/api/market/snapshot").json()
    assert first["snapshot"] == {"old": True} and first["age_seconds"] >= 120 and first["job_id"] == "job1"
    again = client.get("/api/market/snapshot").json()
    assert again["job_id"] == "job1" and jobs["started"] == ["market_snapshot"]          # the same job


# Without a database (no jobs table) the snapshot is fetched inside the request instead.
def test_snapshot_without_database(client, monkeypatch):
    def no_db(*a, **k):
        raise ConnectionError("database not running")
    monkeypatch.setattr(market, "start_job", no_db)
    monkeypatch.setattr(market, "get_snapshot", fake_snapshot)
    body = client.get("/api/market/snapshot").json()
    assert body["job_id"] is None and body["snapshot"]["stock_count"] == 3


# The sector heatmap: rounded changes, skipped sectors in the note, and a friendly 400 for a wrong window.
def test_sectors(client, monkeypatch):
    from tests.test_overview import fake_sector_data
    calls = []
    monkeypatch.setattr(market, "sector_history", lambda: calls.append(1) or fake_sector_data())
    body = client.get("/api/market/sectors", params={"window": "1 week"}).json()
    assert body["windows"] == ["1 day", "1 week", "1 month"] and body["skipped"] == ["Media"]
    assert len(body["rows"]) == 9 and body["rows"][0]["pct_change"] == round((1.01 ** 5 - 1) * 100, 2)
    assert "Skipped" in body["note"] and body["chart"]["data"][0]["type"] == "treemap"
    client.get("/api/market/sectors")
    assert calls == [1]                                   # kept for 5 minutes, any window
    bad = client.get("/api/market/sectors", params={"window": "1 year"})
    assert bad.status_code == 400 and bad.json()["error"].startswith("window must be one of")
