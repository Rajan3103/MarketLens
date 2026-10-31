"""Tests for marketlens/momentum/ranking.py with made-up prices (no internet, no database)."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from marketlens.momentum import ranking
from marketlens.momentum.ranking import (MomentumRanker, MomentumSettings, biggest_move_pct, history_days,
                                         load_ranker, momentum_scores, traded_value_cr)

DAYS = pd.bdate_range("2024-01-01", periods=400)


# Prices growing by a fixed % each day (rate per symbol), with optional noise.
def growing(rates: dict, noise: float = 0.0, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = {}
    for sym, rate in rates.items():
        daily = rate + noise * rng.standard_normal(len(DAYS))
        data[sym] = 100 * np.cumprod(1 + daily)
    return pd.DataFrame(data, index=DAYS)


# An index that always rises (index filter says yes) or always falls (says no).
def index_line(rate: float) -> pd.Series:
    return pd.Series(20000 * (1 + rate) ** np.arange(len(DAYS)), index=DAYS)


# Settings with no filters, so only the score matters.
def plain(**kw) -> MomentumSettings:
    return MomentumSettings(ma_days=0, index_filter=False, **kw)


# ---- the score ----

def test_return_score_by_hand():
    close = pd.DataFrame({"A": np.arange(1.0, 401.0)}, index=DAYS)   # close = row number + 1
    date = DAYS[299]                                                 # row 299, close 300
    score = momentum_scores(close, date, lookbacks=[10], weights=[1], skip_recent=5)
    # ends on row 294 (close 295), starts on row 284 (close 285)
    assert score["A"] == pytest.approx(295 / 285 - 1)


def test_weights_average_the_lookbacks():
    close = pd.DataFrame({"A": np.arange(1.0, 401.0)}, index=DAYS)
    date = DAYS[299]
    one = momentum_scores(close, date, [10], [1], skip_recent=0)["A"]
    two = momentum_scores(close, date, [20], [1], skip_recent=0)["A"]
    both = momentum_scores(close, date, [10, 20], [1, 3], skip_recent=0)["A"]
    assert both == pytest.approx((one + 3 * two) / 4)


def test_methods_by_hand():
    close = growing({"A": 0.002, "B": 0.001}, noise=0.01)
    date = DAYS[350]
    window = close.loc[:date].iloc[-1 - 21 - 63:-21]
    daily = window.pct_change().iloc[1:]
    vol = daily.std() * math.sqrt(252)
    change = window.iloc[-1] / window.iloc[0] - 1
    vol_adj = momentum_scores(close, date, [63], [1], "vol_adjusted", 21)
    sharpe = momentum_scores(close, date, [63], [1], "sharpe", 21)
    assert vol_adj["A"] == pytest.approx(change["A"] / vol["A"])
    assert sharpe["B"] == pytest.approx(daily["B"].mean() * 252 / vol["B"])


def test_faster_riser_scores_higher_and_short_history_is_nan():
    close = growing({"FAST": 0.003, "SLOW": 0.001, "DOWN": -0.001})
    close.loc[:DAYS[200], "NEW"] = np.nan                              # listed on row 201
    close.loc[DAYS[201]:, "NEW"] = 50.0
    score = momentum_scores(close, DAYS[350])
    assert score["FAST"] > score["SLOW"] > 0 > score["DOWN"]
    assert math.isnan(score["NEW"])                                    # needs 252 + 21 rows of history
    assert momentum_scores(close, DAYS[100]).isna().all()              # too early for anybody


@pytest.mark.parametrize("bad", [dict(method="magic"), dict(weights=[1, 1]), dict(lookbacks=[0, 5, 6]),
                                 dict(weights=[0, 0, 0]), dict(skip_recent=-1)])
def test_bad_settings(bad):
    close = growing({"A": 0.001})
    args = dict(lookbacks=[63, 126, 252], weights=[1, 1, 1], method="return", skip_recent=21) | bad
    with pytest.raises(ValueError):
        momentum_scores(close, DAYS[-1], **args)


# ---- no look-ahead ----

def test_no_look_ahead_in_scores():
    close = growing({"A": 0.002, "B": 0.001, "C": -0.001}, noise=0.01)
    date = DAYS[320]
    before = momentum_scores(close, date, method="sharpe")
    changed = close.copy()
    changed.loc[changed.index > date] *= np.random.default_rng(5).uniform(0.1, 10, (len(DAYS) - 321, 3))
    pd.testing.assert_series_equal(before, momentum_scores(changed, date, method="sharpe"))
    pd.testing.assert_series_equal(before, momentum_scores(close.loc[:date], date, method="sharpe"))


def test_no_look_ahead_in_rank():
    """Wild prices, volumes and index after the date change nothing on that date."""
    close = growing({"A": 0.002, "B": 0.0015, "C": -0.001, "D": 0.001}, noise=0.01)
    volume = pd.DataFrame(5e5, index=DAYS, columns=close.columns)
    index = index_line(0.0005)
    settings = MomentumSettings(min_traded_value_cr=1.0, max_one_day_move_pct=8)
    date = DAYS[330]
    before = MomentumRanker(close, volume, index, settings).rank(date)

    later = close.index > date
    close2, volume2, index2 = close.copy(), volume.copy(), index.copy()
    close2.loc[later] = close2.loc[later] * 50                          # a huge jump the day after
    volume2.loc[later] = 0
    index2.loc[later] = 1.0                                             # and an index crash
    after = MomentumRanker(close2, volume2, index2, settings).rank(date)
    pd.testing.assert_frame_equal(before, after)
    assert before.attrs == after.attrs and before.attrs["market_ok"]


def test_rank_on_a_holiday_uses_the_day_before():
    close = growing({"A": 0.002, "B": 0.001})
    ranker = MomentumRanker(close, settings=plain())
    saturday = DAYS[350].date() + dt.timedelta(days=(5 - DAYS[350].weekday()) % 7)
    friday = saturday - dt.timedelta(days=1)
    table = ranker.rank(saturday)
    assert table.attrs["as_of"] == friday
    pd.testing.assert_frame_equal(table, ranker.rank(friday))


# ---- filters and the rank table ----

def test_rank_table():
    close = growing({"A": 0.001, "B": 0.003, "C": 0.002})
    table = MomentumRanker(close, settings=plain()).rank(DAYS[-1])
    assert list(table.columns) == ["symbol", "score", "rank", "passed", "reason"]
    assert table["symbol"].tolist() == ["B", "C", "A"] and table["rank"].tolist() == [1, 2, 3]
    assert table["passed"].all() and (table["reason"] == "").all()


def test_moving_average_filter():
    close = growing({"UP": 0.002, "FALLING": 0.002})
    close.loc[DAYS[-60]:, "FALLING"] = close.loc[DAYS[-60], "FALLING"] * 0.995 ** np.arange(60)
    table = MomentumRanker(close, settings=MomentumSettings(index_filter=False, skip_recent=63)).rank(DAYS[-1])
    row = table.set_index("symbol")
    assert row.loc["FALLING", "score"] > 0                              # momentum is still good (last 3 months skipped)
    assert not row.loc["FALLING", "passed"] and "200-day average" in row.loc["FALLING", "reason"]
    assert row.loc["UP", "rank"] == 1 and pd.isna(row.loc["FALLING", "rank"])


def test_traded_value_filter():
    close = pd.DataFrame({"BIG": 1000.0, "SMALL": 1000.0}, index=DAYS[:30])
    volume = pd.DataFrame({"BIG": 200_000.0, "SMALL": 20_000.0}, index=DAYS[:30])   # Rs 20 cr and Rs 2 cr a day
    assert traded_value_cr(close, volume, 20).tolist() == pytest.approx([20.0, 2.0])
    full = growing({"BIG": 0.001, "SMALL": 0.002})
    vol = pd.DataFrame({"BIG": 1e6, "SMALL": 1e3}, index=DAYS)
    table = MomentumRanker(full, vol, settings=plain(min_traded_value_cr=5)).rank(DAYS[-1]).set_index("symbol")
    assert table.loc["BIG", "passed"] and not table.loc["SMALL", "passed"]
    assert "traded value Rs" in table.loc["SMALL", "reason"]


def test_one_day_move_filter():
    close = growing({"CALM": 0.001, "JUMPY": 0.002})
    close.loc[DAYS[300]:, "JUMPY"] *= 1.25                             # one 25% jump
    assert biggest_move_pct(close, 252)["JUMPY"] == pytest.approx(25.2, abs=0.1)
    table = MomentumRanker(close, settings=plain(max_one_day_move_pct=15)).rank(DAYS[-1]).set_index("symbol")
    assert table.loc["CALM", "passed"] and "one-day move of 25" in table.loc["JUMPY", "reason"]
    assert MomentumRanker(close, settings=plain(max_one_day_move_pct=15)).rank(DAYS[299])["passed"].all()


@pytest.mark.parametrize("safe, expected", [("LIQUIDBEES", ["LIQUIDBEES"]), ("CASH", [])])
def test_index_filter(safe, expected):
    close = growing({"A": 0.002, "B": 0.001})
    weak = MomentumRanker(close, index_close=index_line(-0.0005), settings=MomentumSettings(safe_asset=safe))
    table = weak.rank(DAYS[-1])
    assert not table.attrs["market_ok"] and table.attrs["hold"] == safe
    assert table.attrs["index_close"] < table.attrs["index_average"]
    assert weak.picks(DAYS[-1]) == expected
    strong = MomentumRanker(close, index_close=index_line(0.0005))
    assert strong.rank(DAYS[-1]).attrs["hold"] is None
    assert strong.picks(DAYS[-1], top_n=1) == ["A"]


def test_missing_inputs_are_explained():
    close = growing({"A": 0.001})
    with pytest.raises(ValueError, match="Nifty 50"):
        MomentumRanker(close)                                          # index filter on, no index
    with pytest.raises(ValueError, match="volumes"):
        MomentumRanker(close, settings=plain(min_traded_value_cr=1))
    with pytest.raises(ValueError, match="no prices"):
        MomentumRanker(close, settings=plain()).rank(dt.date(2020, 1, 1))


# ---- loading from the database (load_prices replaced, so no database is needed) ----

def test_load_ranker_reads_only_up_to_the_date(monkeypatch):
    close = growing({"A": 0.002, "B": 0.001})
    asked = []

    # Stand-in for load_prices: records the dates asked for and returns those rows.
    def fake_load(symbols, start, end, field_name="close", url=None):
        asked.append((tuple(symbols), start, end, field_name))
        if symbols == [ranking.NIFTY_INDEX]:
            return index_line(0.0005).loc[str(start):str(end)].to_frame(ranking.NIFTY_INDEX)
        frame = close if field_name == "close" else close * 0 + 1e6
        return frame.loc[str(start):str(end), symbols]

    monkeypatch.setattr("marketlens.ingest.prices.load_prices", fake_load)
    date = DAYS[360].date()
    settings = MomentumSettings(min_traded_value_cr=1)
    ranker = load_ranker(["A", "B"], date, settings)
    assert {a[2] for a in asked} == {date}                              # never past the date
    assert {a[3] for a in asked} == {"close", "volume"} and any(a[0] == ("^NSEI",) for a in asked)
    assert asked[0][1] == date - dt.timedelta(days=history_days(settings))
    assert ranker.rank(date)["rank"].tolist() == [1, 2]


def test_history_days_cover_the_longest_need():
    s = MomentumSettings()
    trading_days = len(pd.bdate_range(dt.date(2026, 10, 30) - dt.timedelta(days=history_days(s)), "2026-10-30"))
    assert trading_days > 252 + 21 + 15                               # enough even with ~15 holidays a year
