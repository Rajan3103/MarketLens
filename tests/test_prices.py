"""Tests for marketlens/ingest/prices.py with a FAKE yfinance (no internet needed).

FakeYahoo stands in for yf.download: it returns made-up prices and records
which tickers and dates were asked for, so we can check that only the
missing days are downloaded.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from marketlens import utils
from marketlens.ingest import prices
from marketlens.ingest.prices import (fetch_start, frame_to_rows, load_prices, load_universe,
                                      sync_prices, ticker_frame, yf_ticker)
from marketlens.models import DailyPrice, Instrument

START = dt.date(2026, 1, 1)


class FakeYahoo:
    """Pretends to be yf.download. Every weekday has a price; close = base + day number."""

    # base: starting price per ticker; missing: tickers that return nothing; listed: ticker -> first trading day.
    def __init__(self, base: dict, missing=(), bump: float = 0.0, listed: dict | None = None):
        self.base, self.missing, self.bump, self.listed = base, set(missing), bump, listed or {}
        self.calls = []

    # Same arguments as yf.download; returns (ticker, field) columns like the real one.
    def __call__(self, tickers, start, end, **kwargs):
        tickers = [tickers] if isinstance(tickers, str) else list(tickers)
        self.calls.append((tuple(tickers), start, end))
        days = pd.bdate_range(start, end - dt.timedelta(days=1))
        frames = {}
        for t in tickers:
            if t in self.missing or t not in self.base:
                continue
            mine = days[days >= pd.Timestamp(self.listed.get(t, dt.date(1990, 1, 1)))]
            if mine.empty:
                continue
            n = np.array([(d.date() - START).days for d in mine], dtype=float)
            close = self.base[t] + n + self.bump
            frames[t] = pd.DataFrame({"Open": close - 1, "High": close + 2, "Low": close - 2,
                                      "Close": close, "Volume": 1000.0}, index=mine)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, axis=1)


# Swap the real yfinance for a fake one and switch off the retry waits.
@pytest.fixture
def fake_yahoo(monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)

    def install(**kwargs):
        fake = FakeYahoo(**kwargs)
        monkeypatch.setattr(prices.yf, "download", fake)
        return fake
    return install


# ---------------------------------------------------------------------
# No database needed
# ---------------------------------------------------------------------

# NSE symbols get .NS; indices and tickers with a suffix are left alone.
@pytest.mark.parametrize("symbol, expected", [
    ("RELIANCE", "RELIANCE.NS"), (" tcs ", "TCS.NS"), ("M&M", "M&M.NS"),
    ("^NSEI", "^NSEI"), ("NIFTYBEES.NS", "NIFTYBEES.NS"),
])
def test_yf_ticker(symbol, expected):
    assert yf_ticker(symbol) == expected


# Nothing stored -> start; something stored -> the last stored day (overlap check).
def test_fetch_start():
    assert fetch_start(None, START) == START
    assert fetch_start(dt.date(2026, 3, 5), START) == dt.date(2026, 3, 5)
    assert fetch_start(dt.date(2020, 1, 1), START) == START   # older than the window


# All three yfinance column layouts are understood.
def test_ticker_frame_layouts():
    fake = FakeYahoo({"AAA.NS": 100, "BBB.NS": 200})
    by_ticker = fake(["AAA.NS", "BBB.NS"], START, dt.date(2026, 1, 10))
    assert ticker_frame(by_ticker, "BBB.NS")["Close"].iloc[0] == 200  # 1 Jan 2026 (a Thursday) = day 0
    by_field = by_ticker.swaplevel(axis=1)
    assert ticker_frame(by_field, "BBB.NS")["Close"].iloc[0] == 200
    flat = by_ticker["AAA.NS"]
    assert ticker_frame(flat, "AAA.NS")["Close"].iloc[0] == 100
    assert ticker_frame(by_ticker, "ZZZ.NS").empty


# Rows get rounded prices, a plain date and NULL for missing volume.
def test_frame_to_rows():
    frame = pd.DataFrame({"Open": [1.123456], "High": [2.0], "Low": [0.5], "Close": [1.987654],
                          "Volume": [np.nan]}, index=pd.to_datetime(["2026-01-02"]))
    row = frame_to_rows(frame, instrument_id=7)[0]
    assert row["date"] == dt.date(2026, 1, 2)
    assert row["open"] == 1.1235 and row["close"] == 1.9877
    assert row["volume"] is None and row["instrument_id"] == 7


# The saved universe files have the right size and columns.
def test_universe_files():
    assert len(load_universe("nifty50")) == 50
    assert len(load_universe("nifty200")) == 200
    etfs = load_universe("etfs")
    assert {"symbol", "name", "category"} <= set(etfs.columns)
    assert "NIFTYBEES" in set(etfs["symbol"])
    with pytest.raises(ValueError):
        load_universe("sensex")


# ---------------------------------------------------------------------
# With the TEST database
# ---------------------------------------------------------------------

# First sync downloads everything and adds the instruments.
def test_first_sync(db_url, db_session, fake_yahoo):
    fake = fake_yahoo(base={"AAA.NS": 100, "BBB.NS": 200})
    summary = sync_prices(["AAA", "BBB"], START, dt.date(2026, 1, 31), url=db_url)
    assert summary.new_instruments == 2
    assert summary.rows_upserted == 2 * 22          # 22 weekdays in January 2026
    assert not summary.failed
    assert db_session.scalar(select(func.count()).select_from(DailyPrice)) == 44
    assert db_session.scalar(select(Instrument.yf_ticker).where(Instrument.symbol == "AAA")) == "AAA.NS"
    assert fake.calls[0][1] == START


# Second sync only downloads from the last stored day, and adds no duplicates.
def test_second_sync_downloads_only_missing_days(db_url, db_session, fake_yahoo):
    fake_yahoo(base={"AAA.NS": 100})
    sync_prices(["AAA"], START, dt.date(2026, 1, 31), url=db_url)

    fake = fake_yahoo(base={"AAA.NS": 100})
    summary = sync_prices(["AAA"], START, dt.date(2026, 2, 13), url=db_url)
    assert fake.calls[0][1] == dt.date(2026, 1, 30)  # last stored weekday, not 1 Jan
    assert summary.new_instruments == 0
    assert db_session.scalar(select(func.count()).select_from(DailyPrice)) == 22 + 10


# Nothing is downloaded when the stored data already reaches the end date.
def test_up_to_date_symbol_is_skipped(db_url, db_session, fake_yahoo):
    fake_yahoo(base={"AAA.NS": 100})
    sync_prices(["AAA"], START, dt.date(2026, 1, 30), url=db_url)
    fake = fake_yahoo(base={"AAA.NS": 100})
    summary = sync_prices(["AAA"], START, dt.date(2026, 1, 30), url=db_url)
    assert summary.up_to_date == ["AAA"] and fake.calls == []


# If Yahoo changed old prices (split/dividend), the whole history is downloaded again.
def test_adjusted_history_is_refreshed(db_url, db_session, fake_yahoo):
    fake_yahoo(base={"AAA.NS": 100})
    sync_prices(["AAA"], START, dt.date(2026, 1, 31), url=db_url)

    fake = fake_yahoo(base={"AAA.NS": 100}, bump=-50)  # every old close now 50 lower
    summary = sync_prices(["AAA"], START, dt.date(2026, 2, 6), url=db_url)
    assert summary.refreshed == ["AAA"]
    assert fake.calls[-1][1] == START                  # second call: full history
    first_close = db_session.scalar(select(DailyPrice.close).order_by(DailyPrice.date))
    assert float(first_close) == 100 + 0 - 50          # old rows were corrected (1 Jan = day 0)


# A symbol Yahoo doesn't know is reported as failed; the others are still saved.
def test_failed_symbol_is_reported(db_url, db_session, fake_yahoo):
    fake_yahoo(base={"AAA.NS": 100}, missing={"OLDCO.NS"})
    summary = sync_prices(["AAA", "OLDCO"], START, dt.date(2026, 1, 9), url=db_url)
    assert summary.failed == ["OLDCO"]
    assert summary.rows_upserted == 7                  # weekdays 1-9 Jan 2026


# load_prices returns dates x symbols, read from the database only.
def test_load_prices(db_url, db_session, fake_yahoo):
    fake_yahoo(base={"AAA.NS": 100, "BBB.NS": 200})
    sync_prices(["AAA", "BBB"], START, dt.date(2026, 1, 31), url=db_url)

    closes = load_prices(["BBB", "AAA", "NOPE"], dt.date(2026, 1, 5), dt.date(2026, 1, 9), url=db_url)
    assert list(closes.columns) == ["BBB", "AAA"]      # requested order, unknown symbol left out
    assert len(closes) == 5                            # Mon-Fri
    assert closes.loc["2026-01-05", "AAA"] == 104.0    # 100 + day 4
    assert isinstance(closes.index, pd.DatetimeIndex)
    assert load_prices(["AAA"], dt.date(2030, 1, 1), url=db_url).empty


# sync_prices reports progress once per batch of symbols (for the page's progress bar).
def test_sync_progress(db_url, db_session, fake_yahoo):
    fake_yahoo(base={"AAA.NS": 100, "BBB.NS": 200})
    steps = []
    sync_prices(["AAA", "BBB"], START, dt.date(2026, 1, 9), url=db_url,
                progress=lambda done, total, text: steps.append((done, total)))
    assert steps == [(1, 1)]


# ---------------------------------------------------------------------
# Older history (backfill)
# ---------------------------------------------------------------------

# The first stored day of AAA.
def first_day(session):
    return session.scalar(select(func.min(DailyPrice.date)))


# A normal sync never goes back before the stored history; backfill adds the older days once.
def test_backfill_adds_older_history(db_url, db_session, fake_yahoo):
    fake_yahoo(base={"AAA.NS": 100})
    sync_prices(["AAA"], dt.date(2026, 1, 15), dt.date(2026, 1, 30), url=db_url)
    assert first_day(db_session) == dt.date(2026, 1, 15)
    sync_prices(["AAA"], dt.date(2025, 12, 1), dt.date(2026, 1, 30), url=db_url)
    assert first_day(db_session) == dt.date(2026, 1, 15)               # the daily sync does not backfill

    fake = fake_yahoo(base={"AAA.NS": 100})
    summary = sync_prices(["AAA"], dt.date(2025, 12, 1), dt.date(2026, 1, 30), url=db_url, backfill=True)
    assert summary.backfilled == ["AAA"] and summary.refreshed == []
    assert fake.calls[-1][1] == dt.date(2025, 12, 1) and fake.calls[-1][2] == dt.date(2026, 1, 16)
    assert first_day(db_session) == dt.date(2025, 12, 1)
    days = pd.bdate_range("2025-12-01", "2026-01-30")
    assert db_session.scalar(select(func.count()).select_from(DailyPrice)) == len(days)   # no gaps, no doubles
    assert "1 older history added" in str(summary)

    again = fake_yahoo(base={"AAA.NS": 100})
    assert sync_prices(["AAA"], dt.date(2025, 12, 1), dt.date(2026, 1, 30), url=db_url, backfill=True).backfilled == []
    assert all(call[1] != dt.date(2025, 12, 1) for call in again.calls)              # nothing older to fetch


# A stock listed later has nothing older: reported, not failed.
def test_backfill_listed_later(db_url, db_session, fake_yahoo):
    fake_yahoo(base={"NEW.NS": 50}, listed={"NEW.NS": dt.date(2026, 1, 12)})
    sync_prices(["NEW"], dt.date(2026, 1, 12), dt.date(2026, 1, 30), url=db_url)
    fake_yahoo(base={"NEW.NS": 50}, listed={"NEW.NS": dt.date(2026, 1, 12)})
    summary = sync_prices(["NEW"], dt.date(2025, 12, 1), dt.date(2026, 1, 30), url=db_url, backfill=True)
    assert summary.no_older == ["NEW"] and summary.backfilled == [] and summary.failed == []
    assert first_day(db_session) == dt.date(2026, 1, 12)


# Yahoo adjusted the prices since they were stored (split / bonus): old and new parts must match, so the
# whole history is downloaded again.
def test_backfill_after_adjustment_downloads_everything(db_url, db_session, fake_yahoo):
    fake_yahoo(base={"AAA.NS": 100})
    sync_prices(["AAA"], dt.date(2026, 1, 15), dt.date(2026, 1, 30), url=db_url)
    fake_yahoo(base={"AAA.NS": 100}, bump=-50)
    summary = sync_prices(["AAA"], dt.date(2025, 12, 1), dt.date(2026, 1, 30), url=db_url, backfill=True)
    assert summary.backfilled == ["AAA"] and "AAA" in summary.refreshed
    closes = dict(db_session.execute(select(DailyPrice.date, DailyPrice.close)).all())
    assert float(closes[dt.date(2026, 1, 30)]) == 100 + 29 - 50                       # the newer part, adjusted too
    assert float(closes[dt.date(2025, 12, 1)]) == 100 - 31 - 50
