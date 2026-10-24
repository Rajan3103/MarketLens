"""Tests for marketlens/ingest/historical.py and broker/dhan_client.py.

A FakeDhan stands in for the real dhanhq client: it records every request and
answers with made-up candles, so no internet and no Dhan key are needed.
"""
import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import func, select

from marketlens import config, utils
from marketlens.broker import dhan_client
from marketlens.broker.dhan_client import DhanError, DhanTemporaryError, unwrap
from marketlens.ingest import historical
from marketlens.ingest.dhan_master import INDICES, Security
from marketlens.ingest.historical import (IST, check_range, csv_name, date_chunks, demo_candles,
                                          download_candles, is_demo, resolve_folder, save_csv,
                                          save_to_db, timeframe_minutes, to_frame)
from marketlens.models import PriceBar

NIFTY = INDICES["NIFTY"]
FUT = Security("NIFTY-Nov2026-FUT", "61471", "NSE_FNO", "FUTIDX", "NSE", "future",
               "NIFTY NOV FUT", dt.date(2026, 11, 23), 65)
TODAY = dt.date.today()


class FakeDhan:
    """Pretends to be dhanhq: one 09:15 candle per weekday between from_date and to_date (excluded)."""

    # Start with no requests recorded.
    def __init__(self):
        self.calls = []

    # Build Dhan-style lists of numbers for the asked dates.
    def _answer(self, from_date, to_date):
        days = pd.bdate_range(from_date, pd.Timestamp(to_date) - pd.Timedelta(days=1))
        stamps = [int(pd.Timestamp(f"{d.date()} 09:15", tz=IST).timestamp()) for d in days]
        n = len(stamps)
        return {"status": "success", "remarks": "", "data": {
            "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
            "close": [100.5 + i for i in range(n)], "volume": [10] * n,
            "timestamp": stamps, "open_interest": [5] * n}}

    # Same arguments as dhanhq.intraday_minute_data.
    def intraday_minute_data(self, security_id, segment, instrument, from_date, to_date,
                             interval=1, oi=False):
        self.calls.append(("intraday", from_date, to_date, interval))
        return self._answer(from_date, to_date)

    # Same arguments as dhanhq.historical_daily_data.
    def historical_daily_data(self, security_id, segment, instrument, from_date, to_date,
                              expiry_code=0, oi=False):
        self.calls.append(("daily", from_date, to_date))
        return self._answer(from_date, to_date)


# Switch off real waiting in @retry and the rate limiter.
@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)
    monkeypatch.setattr(dhan_client.DATA_LIMITER, "_sleep", lambda s: None)


# ---- timeframes and dates ----

# Every timeframe the page offers maps to Dhan's minutes (daily = 1440).
def test_timeframes():
    assert [timeframe_minutes(t) for t in historical.TIMEFRAMES] == [1, 5, 15, 25, 60, 1440]
    with pytest.raises(ValueError):
        timeframe_minutes("2 min")


# Pieces are at most 90 days, with no gaps and no overlaps.
def test_date_chunks():
    chunks = date_chunks(dt.date(2026, 1, 1), dt.date(2026, 4, 30))
    assert chunks == [(dt.date(2026, 1, 1), dt.date(2026, 3, 31)), (dt.date(2026, 4, 1), dt.date(2026, 4, 30))]
    long = date_chunks(dt.date(2022, 1, 1), dt.date(2026, 10, 24))
    assert all((b - a).days + 1 <= 90 for a, b in long)
    assert all(long[i][1] + dt.timedelta(days=1) == long[i + 1][0] for i in range(len(long) - 1))
    assert date_chunks(dt.date(2026, 5, 5), dt.date(2026, 5, 5)) == [(dt.date(2026, 5, 5), dt.date(2026, 5, 5))]


# Impossible ranges get a friendly message.
def test_check_range():
    today = dt.date(2026, 10, 24)
    with pytest.raises(ValueError, match="before"):
        check_range(dt.date(2026, 5, 2), dt.date(2026, 5, 1), "5 min", today)
    with pytest.raises(ValueError, match="5 years"):
        check_range(dt.date(2020, 1, 1), dt.date(2020, 2, 1), "1 min", today)
    check_range(dt.date(2010, 1, 1), dt.date(2020, 1, 1), "1 day", today)   # daily: any age is fine


# Dhan's epoch numbers become Indian times.
def test_to_frame_ist():
    df = to_frame({"open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [7],
                   "timestamp": [1767325500]})  # 2026-01-02 03:45 UTC
    assert str(df.loc[0, "timestamp"]) == "2026-01-02 09:15:00+05:30"
    assert df.loc[0, "source"] == "dhan" and pd.isna(df.loc[0, "oi"])
    assert to_frame({}).empty


# ---- Dhan answers ----

# Success gives the data; rate limits retry; a bad token stops at once.
def test_unwrap():
    assert unwrap({"status": "success", "data": {"x": 1}}) == {"x": 1}
    with pytest.raises(DhanTemporaryError):
        unwrap({"status": "failure", "remarks": {"error_code": "DH-904", "error_message": "Too many"}})
    with pytest.raises(DhanTemporaryError):
        unwrap({"status": "failure", "remarks": "Read timed out"})
    with pytest.raises(DhanError, match="fresh DHAN_ACCESS_TOKEN"):
        unwrap({"status": "failure", "remarks": {"error_code": "DH-901", "error_message": "Invalid"}})


# ---- downloads with the fake Dhan ----

# 4 months of 5-min data = 2 requests (90-day limit), joined into one table.
def test_intraday_download_is_chunked():
    fake = FakeDhan()
    start, end = TODAY - dt.timedelta(days=119), TODAY
    done = []
    df = download_candles(NIFTY, start, end, "5 min", dhan=fake, progress=lambda d, t: done.append((d, t)))
    assert [c[0] for c in fake.calls] == ["intraday", "intraday"]
    assert all(c[3] == 5 for c in fake.calls)
    assert fake.calls[0][1] == start.isoformat()
    assert fake.calls[-1][2] == (end + dt.timedelta(days=1)).isoformat()  # Dhan excludes to_date
    assert done == [(1, 2), (2, 2)]
    assert len(df) == len(pd.bdate_range(start, end)) and df["timestamp"].is_monotonic_increasing
    assert not is_demo(df)


# Daily candles need just one request, even for many years.
def test_daily_download_one_request():
    fake = FakeDhan()
    download_candles(FUT, TODAY - dt.timedelta(days=700), TODAY, "1 day", dhan=fake)
    assert len(fake.calls) == 1 and fake.calls[0][0] == "daily"


# No keys + DEMO_MODE on -> DEMO candles; DEMO_MODE off -> clear "key not set" error.
def test_without_keys(monkeypatch):
    monkeypatch.setattr(historical, "get_dhan", lambda: None)
    monkeypatch.setattr(config, "DEMO_MODE", True)
    df = download_candles(NIFTY, TODAY - dt.timedelta(days=10), TODAY, "15 min")
    assert is_demo(df) and (df["source"] == "DEMO").all()
    monkeypatch.setattr(config, "DEMO_MODE", False)
    with pytest.raises(DhanError, match="key not set"):
        download_candles(NIFTY, TODAY - dt.timedelta(days=10), TODAY, "15 min")


# DEMO candles: weekdays only, inside market hours, sensible prices, repeatable.
@pytest.mark.parametrize("minutes, per_day", [(1, 375), (5, 75), (15, 25), (25, 15), (60, 7)])
def test_demo_candles(minutes, per_day):
    start, end = dt.date(2026, 10, 19), dt.date(2026, 10, 25)  # Mon..Sun
    df = demo_candles(NIFTY, start, end, minutes)
    assert len(df) == 5 * per_day
    times = df["timestamp"].dt.time
    assert times.min() == dt.time(9, 15) and times.max() < dt.time(15, 30)
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()
    assert df.equals(demo_candles(NIFTY, start, end, minutes))


# ---- saving ----

# CSV names say what is inside; DEMO files start with DEMO_.
def test_csv_name():
    assert csv_name(NIFTY, "5 min", dt.date(2026, 1, 1), dt.date(2026, 3, 31), False) == \
        "NIFTY_index_5min_2026-01-01_to_2026-03-31.csv"
    assert csv_name(FUT, "1 day", dt.date(2026, 9, 1), dt.date(2026, 9, 30), True).startswith(
        "DEMO_NIFTY-Nov2026-FUT_future_1day")


# The CSV lands in the chosen folder (created if missing) with IST times.
def test_save_csv(tmp_path):
    df = download_candles(NIFTY, TODAY - dt.timedelta(days=6), TODAY, "60 min", dhan=FakeDhan())
    path = save_csv(df, tmp_path / "new" / "folder", "x.csv")
    assert path.exists()
    back = pd.read_csv(path)
    assert list(back.columns) == historical.COLUMNS and len(back) == len(df)
    assert back["timestamp"].iloc[0].endswith("+05:30")
    assert resolve_folder("data/downloads") == historical.PROJECT_ROOT / "data" / "downloads"


# Real candles go into price_bars; the same download again adds no duplicates.
def test_save_to_db_no_duplicates(db_url, db_session):
    fake = FakeDhan()
    df = download_candles(FUT, TODAY - dt.timedelta(days=13), TODAY, "5 min", dhan=fake)
    assert save_to_db(df, FUT, "5 min", url=db_url) == len(df)
    save_to_db(df, FUT, "5 min", url=db_url)
    assert db_session.scalar(select(func.count()).select_from(PriceBar)) == len(df)
    bar = db_session.scalars(select(PriceBar).order_by(PriceBar.ts)).first()
    assert bar.timeframe == 5 and bar.oi == 5
    assert bar.ts.utcoffset() == dt.timedelta(hours=5, minutes=30)


# DEMO candles are refused by the database.
def test_demo_not_saved(db_url):
    df = demo_candles(NIFTY, dt.date(2026, 10, 19), dt.date(2026, 10, 19), 60)
    with pytest.raises(ValueError, match="DEMO"):
        save_to_db(df, NIFTY, "60 min", url=db_url)
