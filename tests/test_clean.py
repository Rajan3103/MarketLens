"""Tests for marketlens/ingest/clean.py (Yahoo price-error repairs) and how the momentum engine uses them."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketlens.ingest.clean import remove_spikes, repair_scale_errors
from marketlens.momentum import portfolio_bt as pb

DAYS = pd.bdate_range("2020-03-30", periods=8)


# A one-day bad print (up then back down) is replaced by the day before's close.
def test_remove_spikes():
    s = pd.Series([8300.0, 8250, 11168, 8084, 11383, 8792, 8749, 9112], index=DAYS)   # ^NSEI in April 2020
    clean, fixes = remove_spikes(s)
    assert [f["date"] for f in fixes] == [DAYS[2].date(), DAYS[4].date()]
    assert clean.iloc[2] == 8250 and clean.iloc[4] == 8084 and clean.iloc[5] == 8792       # 7-Apr's real +8.8 % stays
    assert fixes[0] == {"date": DAYS[2].date(), "bad": 11168.0, "used": 8250.0}


# Real moves stay: a crash that does not come back, a big rise, small wiggles, gaps.
@pytest.mark.parametrize("values", [
    [100, 100, 87, 85, 86, 88, 90, 91],             # -13 % and stays down (23-Mar-2020)
    [100, 101, 115, 117, 118, 116, 117, 118],       # +14 % and stays up
    [100, 101, 100, 102, 101, 103, 102, 104],       # normal days
    [100, np.nan, 130, 100, np.nan, 99, 100, 101],  # a gap next to the jump: not judged
])
def test_real_moves_are_kept(values):
    s = pd.Series(values, index=DAYS, dtype=float)
    clean, fixes = remove_spikes(s)
    assert fixes == [] and clean.equals(s)


# The momentum engine: index rows on NSE holidays are dropped, ETF x10 errors and index bad prints repaired,
# and every repair is listed.
def test_repair_data():
    days = pd.bdate_range("2019-12-16", "2019-12-30").drop(pd.Timestamp("2019-12-25"))      # NSE shut on Christmas
    stocks = pd.DataFrame({"AAA": np.linspace(100, 110, 10)}, index=days)
    index = pd.Series(np.linspace(12000, 12100, 11),
                      index=days.append(pd.DatetimeIndex(["2019-12-25"])).sort_values())   # a holiday row
    index.loc["2019-12-25"] = 9000.0                                                         # with a wrong value
    index.iloc[3] = 15000.0                                                                  # and a bad print
    etf = pd.Series(np.linspace(129, 131, 10), index=days)
    etf.iloc[4] = 13.0                                                                       # 1:10 split error
    data = pb.repair_data(pb.StockData(stocks, None, index, nifty_etf=etf))
    assert pd.Timestamp("2019-12-25") not in data.index_close.index
    assert data.index_close.max() < 12200 and data.nifty_etf.min() > 120
    kinds = {(f["symbol"], f["kind"].split()[0]) for f in data.fixes}
    assert ("^NSEI", "1") in kinds and ("^NSEI", "bad") in kinds and ("NIFTYBEES", "x10") in kinds
    assert data.close.equals(stocks)                                                         # stocks untouched


# The repairs show up in the results' notes.
def test_fixes_are_reported():
    data = pb.demo_stock_data(n=10, start=dt.date(2017, 6, 1), end=dt.date(2019, 12, 31))
    data.fixes = [{"symbol": "^NSEI", "date": dt.date(2018, 1, 26), "factor": None, "kind": "bad print 9,000.00"}]
    s = pb.StockMomentumSettings(universe="nifty50", start=dt.date(2019, 1, 1), end=dt.date(2019, 12, 31), top_n=3,
                                 hold_rank=3, filters=pb.MomentumSettings(lookbacks=[63], weights=[1], skip_recent=0,
                                                                          ma_days=0, index_filter=False))
    r = pb.run_momentum_backtest(s, data=data)
    assert r.extras["data_fixes"] == data.fixes
    assert any("1 price error(s) in Yahoo's data were repaired before the test (^NSEI 26-Jan-2018)" in n
               for n in r.extras["notes"])


# x10 errors are still found by the older repair (used by the ETF module too).
def test_scale_errors_still_repaired():
    closes = pd.DataFrame({"GOLDBEES": [33.6, 0.34, 33.65, 33.7]}, index=pd.bdate_range("2019-12-18", periods=4))
    fixed, _, fixes = repair_scale_errors(closes)
    assert fixed["GOLDBEES"].round(2).tolist() == [33.6, 34.0, 33.65, 33.7] and len(fixes) == 1
