"""Tests for marketlens/overview.py (Live Market Overview logic). No internet: yfinance and Dhan are faked."""
import datetime as dt

import pandas as pd
import pytest

from marketlens import overview
from marketlens.broker.candles import IST
from marketlens.broker.dhan_instruments import Security
from marketlens.overview import (INDEX_CARDS, SOURCE_DHAN, SOURCE_YF, add_changes, breadth, change_colour,
                                 closes_from_yf, get_snapshot, market_is_open, market_status, top_movers)


# A table of stocks with given % changes (previous close 100, so change = % change).
def stocks(changes: dict) -> pd.DataFrame:
    df = pd.DataFrame({"symbol": list(changes), "name": list(changes),
                       "last": [100 + c for c in changes.values()], "prev_close": 100.0})
    return add_changes(df)


# ---- market hours ----

@pytest.mark.parametrize("when, is_open", [
    (dt.datetime(2026, 10, 28, 9, 14), False),     # Wednesday, one minute early
    (dt.datetime(2026, 10, 28, 9, 15), True),
    (dt.datetime(2026, 10, 28, 12, 0), True),
    (dt.datetime(2026, 10, 28, 15, 30), True),
    (dt.datetime(2026, 10, 28, 15, 31), False),
    (dt.datetime(2026, 10, 31, 11, 0), False),     # Saturday
])
def test_market_is_open(when, is_open):
    assert market_is_open(when.replace(tzinfo=IST)) is is_open


# Times in another time zone are converted to IST first (04:00 UTC = 09:30 IST).
def test_market_hours_use_ist():
    assert market_is_open(dt.datetime(2026, 10, 28, 4, 0, tzinfo=dt.timezone.utc))
    assert market_status(dt.datetime(2026, 10, 31, 11, tzinfo=IST)) == "Market closed, showing last close."


# ---- maths ----

# Change and % change from last price and previous close; rows without prices are dropped.
def test_add_changes():
    df = add_changes(pd.DataFrame({"symbol": ["A", "B", "C"], "name": ["A", "B", "C"],
                                   "last": [110.0, 95.0, None], "prev_close": [100.0, 100.0, 50.0]}))
    assert list(df["symbol"]) == ["A", "B"]
    assert list(df["change"]) == [10.0, -5.0] and list(df["pct_change"]) == [10.0, -5.0]


# Gainers biggest first, losers biggest fall first; never a falling stock among gainers.
def test_top_movers():
    df = stocks({"A": 3, "B": -1, "C": 5, "D": -4, "E": 0, "F": 1, "G": 2, "H": 4, "I": -2})
    gainers, losers = top_movers(df, 5)
    assert list(gainers["symbol"]) == ["C", "H", "A", "G", "F"]
    assert list(losers["symbol"]) == ["D", "I", "B"]          # only 3 stocks fell
    assert (gainers["pct_change"] > 0).all() and (losers["pct_change"] < 0).all()


# Advances, declines, unchanged and the A/D ratio.
def test_breadth():
    b = breadth(stocks({"A": 1, "B": 2, "C": -1, "D": 0, "E": 3}))
    assert b == {"advances": 3, "declines": 1, "unchanged": 1, "ad_ratio": 3.0}
    assert breadth(stocks({"A": 1, "B": 2}))["ad_ratio"] is None          # nothing fell
    assert breadth(stocks({"A": 0.05, "B": -0.04}), flat_pct=0.1)["unchanged"] == 2


# Green up, red down, grey flat.
def test_change_colour():
    assert "16a34a" in change_colour(1.2) and "dc2626" in change_colour(-0.1)
    assert change_colour(0) == "color: gray"


# ---- yfinance (faked) ----

# A fake yfinance download: two days of closes per ticker, (ticker, field) columns.
def fake_yf_data(closes: dict) -> pd.DataFrame:
    days = pd.to_datetime(["2026-10-27", "2026-10-28"])
    frames = {t: pd.DataFrame({"Close": c}, index=days) for t, c in closes.items()}
    return pd.concat(frames, axis=1)


# Last price and previous close come from the last two rows.
def test_closes_from_yf():
    data = fake_yf_data({"^NSEI": [25000.0, 25250.0]})
    assert closes_from_yf(data, "^NSEI") == (25250.0, 25000.0)
    assert closes_from_yf(data, "MISSING.NS") == (None, None)


# Without Dhan keys the snapshot comes from yfinance and says it is delayed.
def test_snapshot_from_yfinance(monkeypatch):
    closes = {t: [100.0, 101.0] for _, _, t in INDEX_CARDS}
    closes.update({"AAA.NS": [100.0, 105.0], "BBB.NS": [100.0, 98.0]})
    monkeypatch.setattr(overview, "_yf_closes", lambda tickers: fake_yf_data(closes))
    monkeypatch.setattr(overview, "load_universe", lambda name: pd.DataFrame(
        {"symbol": ["AAA", "BBB"], "name": ["Aaa Ltd", "Bbb Ltd"]}))

    class NoKeys:
        def is_ready(self):
            return False
    snap = get_snapshot(NoKeys())
    assert snap.source == SOURCE_YF and "15 minutes" in snap.source
    assert list(snap.indices["name"]) == ["Nifty 50", "Bank Nifty", "Fin Nifty", "Sensex", "India VIX"]
    assert snap.indices["pct_change"].round(6).eq(1.0).all()
    assert list(snap.stocks["pct_change"]) == [5.0, -2.0]


# ---- Dhan (faked) ----

class FakeDhanClient:
    """Answers get_quote like DhanDataClient, or fails if told to."""

    # fail: raise an error instead of answering (like an expired token).
    def __init__(self, fail=False):
        self.fail = fail
        self.asked = []

    def is_ready(self):
        return True

    # One quote row per security: last price 101, previous close 100.
    def get_quote(self, secs):
        if self.fail:
            raise RuntimeError("token expired")
        self.asked = [s.symbol for s in secs]
        return pd.DataFrame({"symbol": self.asked, "ltp": 101.0, "close": 100.0})


# Make the Dhan path work without the instrument list file.
@pytest.fixture
def dhan_lookups(monkeypatch):
    monkeypatch.setattr(overview, "load_master", lambda: None)
    monkeypatch.setattr(overview, "find_stock", lambda s, m=None: Security(s, "1", "NSE_EQ", "EQUITY", "NSE", "stock"))
    monkeypatch.setattr(overview, "load_universe", lambda name: pd.DataFrame({"symbol": ["AAA"], "name": ["Aaa"]}))


# With Dhan ready, one quote request covers the 5 indices (VIX = ID 21) and the stocks.
def test_snapshot_from_dhan(dhan_lookups):
    client = FakeDhanClient()
    snap = get_snapshot(client)
    assert snap.source == SOURCE_DHAN
    assert client.asked == ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "INDIAVIX", "AAA"]
    assert snap.indices["pct_change"].round(6).eq(1.0).all() and len(snap.stocks) == 1


# If Dhan fails (e.g. expired token) the page still works, from yfinance.
def test_dhan_failure_falls_back(dhan_lookups, monkeypatch):
    closes = {t: [100.0, 102.0] for _, _, t in INDEX_CARDS}
    closes["AAA.NS"] = [100.0, 99.0]
    monkeypatch.setattr(overview, "_yf_closes", lambda tickers: fake_yf_data(closes))
    snap = get_snapshot(FakeDhanClient(fail=True))
    assert snap.source == SOURCE_YF and snap.stocks.loc[0, "pct_change"] == -1.0


# ---- sector heatmap ----

# Fake sector history: every sector rises 1% a day for 30 days, except Media (1 close only).
def fake_sector_data() -> pd.DataFrame:
    days = pd.bdate_range("2026-09-01", periods=30)
    frames = {}
    for sector, ticker in overview.SECTOR_INDICES:
        values = [100 * 1.01 ** i for i in range(30)] if sector != "Media" else [None] * 29 + [1637.4]
        frames[ticker] = pd.DataFrame({"Close": values}, index=days)
    return pd.concat(frames, axis=1)


# % change over 1 day, 1 week (5 trading days) and 1 month (21 trading days).
@pytest.mark.parametrize("window, back", [("1 day", 1), ("1 week", 5), ("1 month", 21)])
def test_sector_changes(window, back):
    table, skipped = overview.sector_changes(fake_sector_data(), window)
    assert len(table) == 9 and skipped == ["Media"]
    assert table["pct_change"].round(6).eq(round((1.01 ** back - 1) * 100, 6)).all()
    assert list(table["sector"])[:3] == ["Bank", "IT", "Auto"]


# A sector with too little data is skipped AND written to the log.
def test_sector_skip_is_logged(monkeypatch):
    messages = []
    monkeypatch.setattr(overview.log, "warning", lambda msg, *a: messages.append(msg % a))
    data = fake_sector_data().drop(columns="^CNXIT", level=0)     # IT missing completely
    table, skipped = overview.sector_changes(data, "1 day")
    assert skipped == ["IT", "Media"] and "IT" not in set(table["sector"])
    assert any("IT (^CNXIT)" in m for m in messages) and any("Media" in m for m in messages)


# ---- price chart with indicators ----

# 300 made-up trading days of rising prices, with volume.
def chart_prices(days=300) -> pd.DataFrame:
    dates = pd.bdate_range("2025-06-02", periods=days)
    close = pd.Series([100 + i * 0.5 + (i % 7) for i in range(days)], index=dates, dtype=float)
    return pd.DataFrame({"Open": close - 1, "High": close + 2, "Low": close - 2, "Close": close,
                         "Volume": 1000.0}, index=dates)


# Every indicator column is added; the table is cut to the last months but MA200 is ready on its first day.
def test_indicator_table():
    prices = chart_prices()
    table = overview.indicator_table(prices, 10, 30, months=3)
    assert {"MA10", "MA30", "Signal", "MA200", "RSI", "mid", "upper", "lower"} <= set(table.columns)
    assert table.index[0] >= table.index[-1] - pd.DateOffset(months=3) and len(table) < len(prices)
    assert table["MA200"].notna().all() and table["RSI"].between(0, 100).all()
    assert table["MA200"].iloc[-1] == pytest.approx(prices["Close"].iloc[-200:].mean())
    assert (table["lower"] < table["mid"]).all() and (table["mid"] < table["upper"]).all()
    assert len(overview.indicator_table(prices, 10, 30)) == len(prices)         # months=None keeps everything
    with pytest.raises(ValueError, match="must be shorter"):
        overview.indicator_table(prices, 50, 50)


# The chart has 3 panels; the 200-day MA and Bollinger Bands appear only when asked.
def test_indicator_chart():
    table = overview.indicator_table(chart_prices(), 20, 50, months=6)
    names = [t.name for t in overview.indicator_chart(table, 20, 50).data]
    assert names == ["Price", "20-day MA", "50-day MA", "Buy (bullish cross)", "Sell (bearish cross)", "RSI 14",
                     "Volume"]
    full = overview.indicator_chart(table, 20, 50, show_ma200=True, show_bb=True)
    assert {"200-day MA", "Bollinger upper", "Bollinger lower"} <= {t.name for t in full.data}
    assert full.layout.yaxis2.range == (0, 100) and full.layout.yaxis3 is not None


# Indices have no volume on Yahoo Finance.
def test_has_no_volume():
    prices = chart_prices(5)
    assert overview.has_no_volume(prices) is False
    assert overview.has_no_volume(prices.assign(Volume=[0, None, 0, 0, 0])) is True


# The summary behind the cards, movers and breadth.
def test_summarise():
    snap = overview.Snapshot(pd.DataFrame(columns=overview.COLUMNS), stocks({"A": 2, "B": -1, "C": 0}), SOURCE_DHAN,
                             dt.datetime(2026, 11, 3, 10, 0, tzinfo=IST))
    s = overview.summarise(snap, n=5)
    assert list(s["gainers"]["symbol"]) == ["A"] and list(s["losers"]["symbol"]) == ["B"]
    assert s["breadth"]["unchanged"] == 1 and s["stock_count"] == 3 and s["live"] is True


# Breadth bar (3 parts) and sector treemap (one tile per sector).
def test_breadth_and_sector_charts():
    bar = overview.breadth_chart({"advances": 30, "declines": 18, "unchanged": 2, "ad_ratio": 1.67})
    assert [t.name for t in bar.data] == ["Advances", "Unchanged", "Declines"] and bar.data[0].x == (30,)
    table, _ = overview.sector_changes(fake_sector_data(), "1 day")
    tree = overview.sector_chart(table)
    assert tree.data[0].type == "treemap" and len(tree.data[0].labels) == 9
    assert tree.data[0].marker.cmax == pytest.approx(1.0) and tree.data[0].marker.cmin == pytest.approx(-1.0)
