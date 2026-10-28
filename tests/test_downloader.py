"""Tests for the module 3 downloader helpers: CSV/Excel export, resampling option bars,
loading a stored date range, and the "What is stored" summary (DEMO client + TEST database)."""
import datetime as dt
import io

import pandas as pd
import pytest

from marketlens.broker.candles import IST
from marketlens.broker.demo_data import DemoDataClient
from marketlens.export import EXCEL_MAX_ROWS, to_csv_bytes, to_excel_bytes
from marketlens.ingest.options import (download_expired_options, download_index_minutes,
                                       load_option_day, load_option_range, resample_option_bars,
                                       snapshot_option_chain, stored_days)
from marketlens.ingest.stored import what_is_stored

MON, FRI = dt.date(2026, 10, 19), dt.date(2026, 10, 23)


# A DEMO client with fixed numbers (no internet).
@pytest.fixture
def demo():
    return DemoDataClient(spots={"NIFTY": 25000}, vix=14, now=dt.datetime(2026, 10, 28, 11, tzinfo=IST))


# ---- export ----

# Excel cannot hold time zones: times are written as plain IST times and read back the same.
def test_excel_export_with_ist_times():
    df = pd.DataFrame({"ts": pd.to_datetime(["2026-10-21 09:15"]).tz_localize(IST), "close": [101.5]})
    back = pd.read_excel(io.BytesIO(to_excel_bytes({"Day": df})), sheet_name="Day")
    assert str(back.loc[0, "ts"]) == "2026-10-21 09:15:00" and back.loc[0, "close"] == 101.5
    assert b"09:15:00+05:30" in to_csv_bytes(df)          # CSV keeps the +05:30


# A table bigger than Excel allows gives a clear message instead of a broken file.
def test_excel_too_big(monkeypatch):
    import marketlens.export as export
    monkeypatch.setattr(export, "EXCEL_MAX_ROWS", 3)
    with pytest.raises(ValueError, match="CSV"):
        export.to_excel_bytes(pd.DataFrame({"x": range(5)}))
    assert EXCEL_MAX_ROWS == 1_048_575


# ---- resampling ----

# 15 one-minute bars -> 3 five-minute bars with the right open/high/low/close/volume.
def test_resample_option_bars():
    ts = pd.date_range("2026-10-21 09:15", periods=15, freq="1min", tz=IST)
    df = pd.DataFrame({"ts": ts, "strike": 25000.0, "option_type": "CE", "open": range(15),
                       "high": [x + 1 for x in range(15)], "low": [x - 1 for x in range(15)],
                       "close": [x + 0.5 for x in range(15)], "volume": 10, "oi": range(15),
                       "iv": 14.0, "spot": 25000.0, "is_demo": True})
    five = resample_option_bars(df, 5)
    assert list(five["ts"].dt.strftime("%H:%M")) == ["09:15", "09:20", "09:25"]
    first = five.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (0, 5, -1, 4.5)
    assert first["volume"] == 50 and first["oi"] == 4
    assert resample_option_bars(df, 1) is df


# 25-minute candles start at 09:15, 09:40, 10:05 ... like the exchange's.
def test_resample_25_minutes():
    ts = pd.date_range("2026-10-21 09:15", periods=60, freq="1min", tz=IST)
    df = pd.DataFrame({"ts": ts, "strike": 1.0, "option_type": "PE", "open": 1.0, "high": 1.0,
                       "low": 1.0, "close": 1.0, "volume": 1})
    assert list(resample_option_bars(df, 25)["ts"].dt.strftime("%H:%M")) == ["09:15", "09:40", "10:05"]


# ---- database: stored days, ranges and the summary ----

# Weekly options for BANKNIFTY after Nov 2024 are refused before anything is downloaded.
def test_weekly_check(db_url, demo):
    with pytest.raises(ValueError, match="monthly"):
        download_expired_options("BANKNIFTY", MON, FRI, "weekly", ["ATM"], ("CE",), client=demo, url=db_url)


# A stored range comes back as one table with an expiry column; resampling keeps every strike.
def test_load_range_and_resample(db_url, db_session, demo):
    download_index_minutes("NIFTY", MON, FRI, client=demo, url=db_url)
    download_expired_options("NIFTY", MON, FRI, "weekly", ["ATM"], ("CE", "PE"), client=demo, url=db_url)
    assert stored_days("NIFTY", MON, FRI, url=db_url) == [d.date() for d in pd.bdate_range(MON, FRI)]
    rng = load_option_range("NIFTY", MON, FRI, url=db_url)
    assert len(rng) == 2 * 5 * 375 and set(rng["expiry"]) == {dt.date(2026, 10, 20), dt.date(2026, 10, 27)}
    day = load_option_day("NIFTY", dt.date(2026, 10, 21), url=db_url)
    fifteen = resample_option_bars(day, 15)
    assert fifteen["ts"].nunique() == 25
    assert set(fifteen["strike"]) == set(day["strike"])


# "What is stored" lists every kind of data with first/last date, row count and the DEMO flag.
def test_what_is_stored(db_url, db_session, demo):
    assert what_is_stored(url=db_url).empty
    download_index_minutes("NIFTY", MON, FRI, client=demo, url=db_url)
    download_expired_options("NIFTY", MON, FRI, "weekly", ["ATM"], ("CE",), client=demo, url=db_url)
    snapshot_option_chain("NIFTY", client=demo, url=db_url)
    table = what_is_stored(url=db_url).set_index("data")
    candles = table.loc["Candles (Dhan)"]
    assert candles["instrument"] == "NIFTY" and candles["timeframe"] == "1 min"
    assert (candles["first"], candles["last"], candles["rows"]) == (MON, FRI, 5 * 375)
    bars = table.loc["Option minute bars"]
    assert bars["rows"] == 5 * 375 and bars["demo"]
    assert table.loc["Option chain snapshots"]["rows"] == 82
