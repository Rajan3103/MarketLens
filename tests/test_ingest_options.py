"""Tests for marketlens/ingest/options.py, using the DEMO client and the TEST database.

The DEMO client has fixed prices (no internet), so every run stores the same numbers.
"""
import datetime as dt

import pytest
from sqlalchemy import func, select

from marketlens.broker.candles import IST
from marketlens.broker.demo_data import DemoDataClient
from marketlens.ingest import options as ingest
from marketlens.ingest.options import (day_runs, download_expired_options, download_index_minutes,
                                       load_option_day, snapshot_option_chain, stored_expiries,
                                       strike_labels)
from marketlens.models import DownloadProgress, IntradayOptionPrice, OptionContract, OptionQuote, PriceBar

MON, FRI = dt.date(2026, 10, 19), dt.date(2026, 10, 23)


# A DEMO client with fixed numbers (no internet).
@pytest.fixture
def demo():
    return DemoDataClient(spots={"NIFTY": 25000, "SENSEX": 82000}, vix=14,
                          now=dt.datetime(2026, 10, 28, 11, 0, tzinfo=IST))


# ---- no database needed ----

# ATM-2 .. ATM+2 labels.
def test_strike_labels():
    assert strike_labels(2) == ["ATM-2", "ATM-1", "ATM", "ATM+1", "ATM+2"]
    assert len(strike_labels()) == 21


# Missing days are grouped into runs; weekends don't break a run, gaps do, runs stay short.
def test_day_runs():
    days = [dt.date(2026, 10, d) for d in (19, 20, 21, 22, 23, 26, 28)]
    assert day_runs(days, 30) == [(dt.date(2026, 10, 19), dt.date(2026, 10, 26)),
                                  (dt.date(2026, 10, 28), dt.date(2026, 10, 28))]
    long = [d.date() for d in __import__("pandas").bdate_range("2026-01-01", "2026-06-30")]
    assert all((b - a).days < 30 for a, b in day_runs(long, 30))


# ---- with the TEST database ----

# The exact log message the prompt asks for appears when DEMO data is used.
def test_demo_message(db_url, db_session, demo, caplog, monkeypatch):
    messages = []
    monkeypatch.setattr(ingest.log, "warning", lambda msg, *a: messages.append(msg % a if a else msg))
    download_index_minutes("NIFTY", MON, MON, client=demo, url=db_url)
    assert "Dhan keys not set: using DEMO minute data" in messages


# Expired options: bars stored per contract, marked DEMO, progress noted, and a rerun skips everything.
def test_download_expired_and_resume(db_url, db_session, demo):
    seen = []
    first = download_expired_options("NIFTY", MON, FRI, "weekly", ["ATM-1", "ATM", "ATM+1"], ("CE", "PE"),
                                     client=demo, url=db_url, progress=lambda d, t, x: seen.append((d, t)))
    assert first["source"] == "DEMO" and first["series"] == 6 and first["pieces"] == 6
    assert first["rows"] == 6 * 5 * 375                     # 6 series x 5 days x 375 minutes
    assert seen[-1] == (6, 6)
    assert db_session.scalar(select(func.count()).select_from(IntradayOptionPrice)) == 6 * 5 * 375
    assert db_session.scalar(select(func.count()).select_from(DownloadProgress)) == 6 * 5
    assert all(db_session.scalars(select(OptionContract.is_demo)))

    again = download_expired_options("NIFTY", MON, FRI, "weekly", ["ATM-1", "ATM", "ATM+1"], ("CE", "PE"),
                                     client=demo, url=db_url)
    assert again["pieces"] == 0 and again["skipped_days"] == 6 * 5   # nothing downloaded twice


# If a download stops in the middle, the finished pieces stay and a rerun completes the rest.
def test_resume_after_stop(db_url, db_session, demo, monkeypatch):
    real = demo.get_expired_options
    calls = {"n": 0}

    # Fail on the second piece, like a network cut.
    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("network down")
        return real(*args, **kwargs)
    monkeypatch.setattr(demo, "get_expired_options", flaky)
    with pytest.raises(ConnectionError):
        download_expired_options("NIFTY", MON, FRI, "weekly", ["ATM"], ("CE", "PE"), client=demo, url=db_url)
    assert db_session.scalar(select(func.count()).select_from(DownloadProgress)) == 5   # only CE done

    monkeypatch.setattr(demo, "get_expired_options", real)
    rest = download_expired_options("NIFTY", MON, FRI, "weekly", ["ATM"], ("CE", "PE"), client=demo, url=db_url)
    assert rest["pieces"] == 1 and rest["skipped_days"] == 5
    assert db_session.scalar(select(func.count()).select_from(IntradayOptionPrice)) == 2 * 5 * 375


# Index minutes go into price_bars (timeframe 1), marked DEMO.
def test_index_minutes(db_url, db_session, demo):
    result = download_index_minutes("NIFTY", MON, FRI, client=demo, url=db_url)
    assert result["rows"] == 5 * 375
    bars = db_session.scalars(select(PriceBar)).all()
    assert len(bars) == 5 * 375 and all(b.timeframe == 1 and b.is_demo for b in bars)
    with pytest.raises(ValueError):
        download_index_minutes("RELIANCE", MON, FRI, client=demo, url=db_url)


# load_option_day: one day, every stored strike, with the spot from the index minutes.
def test_load_option_day(db_url, db_session, demo):
    download_index_minutes("NIFTY", MON, FRI, client=demo, url=db_url)
    download_expired_options("NIFTY", MON, FRI, "weekly", ["ATM-1", "ATM", "ATM+1"], ("CE", "PE"),
                             client=demo, url=db_url)
    day = dt.date(2026, 10, 21)
    expiry = stored_expiries("NIFTY", day, url=db_url)[0]
    assert expiry == dt.date(2026, 10, 27)                    # next Tuesday
    df = load_option_day("NIFTY", day, url=db_url)
    assert df.attrs["expiry"] == expiry and df.attrs["is_demo"]
    assert set(df["option_type"]) == {"CE", "PE"} and df["ts"].dt.date.eq(day).all()
    assert df["spot"].notna().all()
    first_minute = df[df["ts"] == df["ts"].min()]
    atm = round(first_minute["spot"].iloc[0] / 50) * 50
    assert set(first_minute["strike"]) == {atm - 50, atm, atm + 50}
    assert load_option_day("NIFTY", dt.date(2026, 10, 25), url=db_url).empty   # Sunday


# A chain snapshot saves one quote per strike and side, with Greeks and the spot.
def test_snapshot(db_url, db_session, demo):
    result = snapshot_option_chain("NIFTY", client=demo, url=db_url)
    assert result["source"] == "DEMO" and result["expiry"] == dt.date(2026, 11, 3)
    assert result["rows"] == 41 * 2
    quote = db_session.scalars(select(OptionQuote)).first()
    assert quote.delta is not None and float(quote.underlying_price) == 25000


# Real Dhan data removes DEMO rows for that underlying first, so they never mix.
def test_real_data_purges_demo(db_url, db_session, demo):
    download_index_minutes("NIFTY", MON, MON, client=demo, url=db_url)
    assert db_session.scalar(select(func.count()).select_from(PriceBar)) == 375

    class RealLike(DemoDataClient):
        source = "DHAN"
        is_demo = False
    real = RealLike(spots={"NIFTY": 25000}, vix=14, now=demo.now())
    download_index_minutes("NIFTY", MON, MON, client=real, url=db_url)
    db_session.expire_all()
    assert not any(db_session.scalars(select(PriceBar.is_demo)))
    assert db_session.scalar(select(func.count()).select_from(PriceBar)) == 375
