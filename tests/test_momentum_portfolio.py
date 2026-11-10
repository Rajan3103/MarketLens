"""Tests for marketlens/momentum/portfolio_bt.py (module 8, stock momentum backtest vs Nifty 50).

Made-up prices, no internet. Database tests use TEST_DATABASE_URL (skipped when it is not set).
"""
import copy
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketlens.momentum import portfolio_bt as pb
from marketlens.momentum.portfolio import NO_COSTS, TaxSettings
from marketlens.momentum.portfolio_bt import (LIQUID, StockData, StockMomentumSettings, choose, demo_stock_data,
                                              run_momentum_backtest, stock_weights, target_weights)
from marketlens.momentum.ranking import NIFTY_INDEX, MomentumSettings

START, END = dt.date(2019, 1, 1), dt.date(2021, 12, 31)


# Quick settings on made-up data: no 200-day average or index filter unless asked.
def settings(**kw) -> StockMomentumSettings:
    filters = kw.pop("filters", MomentumSettings(lookbacks=[63], weights=[1], skip_recent=0, ma_days=0,
                                                 index_filter=False))
    base = dict(universe="nifty50", start=START, end=END, top_n=5, hold_rank=5, filters=filters,
                tax=TaxSettings(enabled=False))
    return StockMomentumSettings(**{**base, **kw})


@pytest.fixture(scope="module")
def data() -> StockData:
    return demo_stock_data(n=25, start=dt.date(2017, 6, 1), end=END)


# A ranking table like MomentumRanker.rank(): symbols in rank order, the last ones failed the filters.
def ranks(symbols: list[str], failed: int = 0) -> pd.DataFrame:
    n = len(symbols)
    table = pd.DataFrame({"symbol": symbols, "score": np.linspace(1, 0, n), "passed": [True] * (n - failed) + [False] * failed,
                          "reason": [""] * (n - failed) + ["price not above its 200-day average"] * failed})
    table.insert(2, "rank", pd.array([i + 1 if ok else pd.NA for i, ok in enumerate(table["passed"])], dtype="Int64"))
    table.attrs["market_ok"] = True
    return table


# ---------------------------------------------------------------- choosing and weighing

# The "hold while in top M" buffer: a held stock ranked 25 stays (M = 30); one ranked 31 is sold.
def test_top_m_buffer_rule():
    table = ranks([f"S{i}" for i in range(1, 41)])            # S1 = rank 1 ... S40 = rank 40
    picked = choose(table, held=["S25", "S31", "S3"], top_n=20, hold_rank=30)
    assert "S25" in picked and "S31" not in picked and len(picked) == 20
    assert picked[:2] == ["S3", "S25"]                         # kept ones first, best rank first
    assert picked[2:] == [f"S{i}" for i in (1, 2, *range(4, 20))]
    assert choose(table, held=["S25"], top_n=20, hold_rank=20) == [f"S{i}" for i in range(1, 21)]  # M = N: no buffer


# A held stock that fails a filter (no rank) is always sold, however good it was.
def test_buffer_never_keeps_a_failed_stock():
    table = ranks(["A", "B", "C", "D"], failed=1)              # D failed the 200-day average
    assert choose(table, held=["D"], top_n=2, hold_rank=4) == ["A", "B"]


# Inverse-volatility weights add up to 1, and the calmest stock gets the most.
def test_inverse_volatility_weights_add_up_to_1():
    vols = pd.Series({"CALM": 0.10, "MID": 0.20, "WILD": 0.40, "NEW": np.nan})
    w = stock_weights(["CALM", "MID", "WILD", "NEW"], "inverse_vol", vols)
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["CALM"] == pytest.approx(2 * w["MID"]) == pytest.approx(4 * w["WILD"])
    assert w["NEW"] == pytest.approx(w["CALM"] * 0.10 / vols.mean())          # missing vol -> the average vol
    assert stock_weights(["A", "B"], "equal") == {"A": 0.5, "B": 0.5}
    assert stock_weights([], "inverse_vol", vols) == {}


# Fewer stocks than N passed: each keeps its 1/N share and the rest goes to LIQUIDBEES (or stays cash).
def test_target_weights_fill_with_safe_asset():
    s = settings(top_n=4, hold_rank=4, weighting="inverse_vol")
    table = ranks(["A", "B", "C", "D", "E"], failed=3)
    weights, chosen = target_weights(table, [], s, pd.Series({"A": 0.2, "B": 0.4}))
    assert chosen == ["A", "B"] and sum(weights.values()) == pytest.approx(1.0)
    assert weights[LIQUID] == pytest.approx(0.5) and weights["A"] == pytest.approx(2 * weights["B"])
    s.filters.safe_asset = "CASH"
    weights, _ = target_weights(table, [], s, None)
    assert LIQUID not in weights and sum(weights.values()) == pytest.approx(0.5)
    table.attrs["market_ok"] = False                           # index filter says no
    assert target_weights(table, ["A"], s, None) == ({}, [])


# ---------------------------------------------------------------- the backtest

# No look-ahead: changing every price AFTER a trade day cannot change anything up to that day.
def test_no_look_ahead(data):
    s = settings()
    full = run_momentum_backtest(s, data=data)
    cut = full.extras["rebalances"].iloc[10]["trade_day"]
    changed = copy.deepcopy(data)
    after = changed.close.index > pd.Timestamp(cut)
    changed.close.loc[after] = changed.close.loc[after].iloc[:, ::-1].to_numpy() * 3     # scramble the future
    changed.index_close.loc[after] *= 0.5
    again = run_momentum_backtest(s, data=changed)
    log_a, log_b = (r.extras["holdings_log"] for r in (full, again))
    assert log_a[log_a["rebalance_date"] <= cut].equals(log_b[log_b["rebalance_date"] <= cut])
    assert full.equity.loc[:cut].equals(again.equity.loc[:cut])
    assert not full.equity.equals(again.equity)                 # the future did change


# Signals use one day's close; the trade is on the NEXT trading day.
def test_trades_the_day_after_the_signal(data):
    log = run_momentum_backtest(settings(), data=data).extras["rebalances"]
    assert (pd.to_datetime(log["trade_day"]) > pd.to_datetime(log["signal_day"])).all()
    assert (pd.to_datetime(log["trade_day"]) - pd.to_datetime(log["signal_day"])).dt.days.max() <= 4


# Costs and tax lower the return; without them it is higher.
def test_costs_lower_the_return(data):
    free = run_momentum_backtest(settings(costs=NO_COSTS), data=data)
    costly = run_momentum_backtest(settings(), data=data)
    taxed = run_momentum_backtest(settings(tax=TaxSettings()), data=data)
    assert free.extras["charges"] == 0 and costly.extras["charges"] > 0
    assert costly.metrics["total_return_pct"] < free.metrics["total_return_pct"]
    assert taxed.extras["tax_paid"] > 0 and taxed.metrics["total_return_pct"] < costly.metrics["total_return_pct"]


# The buffer trades less than "always exactly the top N".
def test_buffer_trades_less(data):
    tight = run_momentum_backtest(settings(top_n=5, hold_rank=5), data=data)
    loose = run_momentum_backtest(settings(top_n=5, hold_rank=12), data=data)
    assert loose.extras["orders"] < tight.extras["orders"]


# The value curve (worked out for all days at once) matches the portfolio's own value after each rebalance.
def test_equity_matches_the_book(data):
    r = run_momentum_backtest(settings(weighting="inverse_vol", rebalance="weekly"), data=data)
    log = r.extras["rebalances"]
    for row in log.itertuples():
        assert r.equity.loc[row.trade_day] == pytest.approx(row.value_after, abs=0.02)
    assert r.equity.index[0] == pd.Timestamp(START).date() or r.equity.index[0] > START
    held = r.extras["holdings_log"]
    last = held[held["rebalance_date"] == held["rebalance_date"].max()]
    assert last["weight_pct"].sum() == pytest.approx(100, abs=0.1)
    assert r.benchmark is not None and r.benchmark.iloc[0] == pytest.approx(r.capital)


# Index filter: when Nifty 50 is below its average everything goes to LIQUIDBEES.
def test_index_filter_moves_to_liquidbees(data):
    falling = copy.deepcopy(data)
    falling.index_close[:] = np.linspace(20_000, 10_000, len(falling.index_close))      # always below its average
    s = settings(filters=MomentumSettings(lookbacks=[63], weights=[1], skip_recent=0, ma_days=0,
                                          index_filter=True, index_ma_days=50))
    r = run_momentum_backtest(s, data=falling)
    held = set(r.extras["holdings_log"]["symbol"])
    assert held <= {LIQUID, "Cash"} and r.extras["days_in_cash"] == len(r.extras["rebalances"])


# A Momentum 30 ETF listed later is shown from its first day, starting at the strategy's value that day.
def test_momentum_tracker_benchmark(data):
    with_etf = copy.deepcopy(data)
    days = with_etf.close.index[with_etf.close.index >= "2020-06-01"]
    with_etf.trackers = {"Nifty 200 Momentum 30 (MOMENTUM ETF)": pd.Series(np.linspace(30, 40, len(days)), index=days)}
    r = run_momentum_backtest(settings(), data=with_etf)
    name = next(n for n in r.extras["benchmarks"] if "Momentum 30" in n)
    assert "from 01-Jun-2020" in name
    first = r.extras["benchmarks"][name].index[0]
    assert r.extras["benchmarks"][name].iloc[0] == pytest.approx(r.equity.loc[first], abs=0.01)
    table = pb.comparison_table(r)
    assert list(table.columns) == ["Measure", "Strategy", pb.NIFTY_NAME, name]


# NIFTYBEES (dividends included, like the stocks) is the main benchmark when it covers the period; the ^NSEI price
# index is kept as a second line. A NIFTYBEES series that starts late is not used as the main line.
def test_niftybees_is_the_fair_benchmark(data):
    with_etf = copy.deepcopy(data)
    with_etf.nifty_etf = with_etf.index_close * 1.0004 ** np.arange(len(with_etf.index_close)) / 100   # + dividends
    r = run_momentum_backtest(settings(), data=with_etf)
    assert r.benchmark_name == pb.NIFTY_ETF_NAME and list(r.extras["benchmarks"])[:2] == [pb.NIFTY_ETF_NAME, pb.NIFTY_NAME]
    assert r.benchmark.iloc[0] == pytest.approx(r.capital) and r.benchmark.iloc[-1] > r.extras["benchmarks"][pb.NIFTY_NAME].iloc[-1]
    late = copy.deepcopy(data)
    late.nifty_etf = late.index_close[late.index_close.index >= "2020-06-01"]
    r2 = run_momentum_backtest(settings(), data=late)
    assert r2.benchmark_name == pb.NIFTY_NAME and pb.NIFTY_ETF_NAME not in r2.extras["benchmarks"]


# Settings survive the trip to JSON and back; bad ones give a clear message.
def test_settings_round_trip_and_checks():
    s = settings(weighting="inverse_vol", hold_rank=8, end=None)
    back = StockMomentumSettings.from_params(s.to_params())
    assert back == s and isinstance(back.filters, MomentumSettings)
    assert StockMomentumSettings.from_params({"top_n": 10}).top_n == 10           # the rest keeps defaults
    for bad, words in [({"hold_rank": 3}, "top M"), ({"universe": "nifty500"}, "universe"),
                       ({"weighting": "cap"}, "weighting"), ({"top_n": 60}, "top N"), ({"end": START}, "end date")]:
        with pytest.raises(ValueError, match=words):
            settings(**bad).check()
    with pytest.raises(ValueError, match="Unknown setting"):
        StockMomentumSettings.from_params({"filters": {"colour": "red"}})


# 10 years of 200 stocks, weekly, inverse volatility: well inside the 60-second limit (NFR-02).
def test_speed_ten_years_of_nifty_200():
    import time
    big = demo_stock_data(n=200, start=dt.date(2014, 6, 1), end=dt.date(2025, 12, 31))
    s = StockMomentumSettings(universe="nifty200", start=dt.date(2016, 1, 1), end=dt.date(2025, 12, 31),
                              rebalance="weekly", weighting="inverse_vol")
    began = time.perf_counter()
    r = run_momentum_backtest(s, data=big)
    assert time.perf_counter() - began < 60 and len(r.extras["rebalances"]) > 500


# ---------------------------------------------------------------- database

# Five made-up stocks and Nifty 50 stored in daily_prices; the universe is just these five.
@pytest.fixture
def stored(db_url, db_session, monkeypatch):
    from marketlens.db import upsert
    from marketlens.ingest.prices import ensure_instruments
    from marketlens.models import DailyPrice
    demo = demo_stock_data(n=5, start=dt.date(2017, 1, 1), end=dt.date(2021, 12, 31), seed=3)
    closes = demo.close.assign(**{NIFTY_INDEX: demo.index_close})
    ids, _ = ensure_instruments(db_session, list(closes.columns))
    rows = [{"instrument_id": ids[sym], "date": day.date(), "open": c, "high": c, "low": c, "close": c, "volume": 1000}
            for sym in closes.columns for day, c in closes[sym].items()]
    upsert(db_session, DailyPrice, rows, ["instrument_id", "date"])
    db_session.commit()
    monkeypatch.setattr(pb, "universe_table", lambda name: pd.DataFrame(
        {"symbol": list(demo.close.columns), "name": [f"Stock {i}" for i in range(5)], "sector": "IT"}))
    return db_url


# Missing prices are listed with their dates; nothing missing once everything is stored.
def test_price_problems(stored, monkeypatch):
    s = settings(top_n=2, hold_rank=3, end=dt.date(2021, 12, 31))
    assert pb.price_problems(s, url=stored) == []
    late = settings(top_n=2, hold_rank=3, end=dt.date(2022, 6, 30))
    problems = pb.price_problems(late, url=stored)
    assert len(problems) == 6 and "stored only up to 31-Dec-2021" in problems[0]["problem"]
    monkeypatch.setattr(pb, "universe_table", lambda name: pd.DataFrame({"symbol": ["NOPE"], "name": ["x"]}))
    with pytest.raises(pb.MissingPricesError, match="NOPE \\(nothing stored"):
        pb.load_stock_data(s, url=stored)


# Stored history that starts too late for the chosen start: a warning with the earliest fair start.
def test_history_warning(stored):
    assert pb.history_warning(settings(top_n=2, hold_rank=3), url=stored) is None          # 2019 start: enough
    early = settings(top_n=2, hold_rank=3, start=dt.date(2017, 3, 1),
                     filters=MomentumSettings(lookbacks=[126], weights=[1], skip_recent=0, ma_days=0, index_filter=False))
    text = pb.history_warning(early, url=stored)
    assert "stored only from 02-Jan-2017" in text and "choose a start of" in text and "Download missing prices" in text
    result = run_momentum_backtest(early, url=stored)
    assert result.extras["first_invested"] > dt.date(2017, 6, 1)
    assert any("The first stocks were bought on" in n for n in result.extras["notes"])


# A run from stored prices is saved with its holdings after every rebalance, and read back as buys / sells.
def test_save_run_and_rebalance_history(stored):
    from sqlalchemy import func, select

    from marketlens.models import PortfolioHolding
    s = settings(top_n=2, hold_rank=3, end=dt.date(2021, 12, 31))
    result = run_momentum_backtest(s, url=stored)
    sid = pb.save_momentum_strategy("Momentum top 2", s, url=stored)
    run_id = pb.save_momentum_run(result, strategy_id=sid, url=stored)
    from marketlens.db import get_session
    with get_session(stored) as session:
        rows = session.scalar(select(func.count()).select_from(PortfolioHolding).where(PortfolioHolding.run_id == run_id))
    log = result.extras["holdings_log"]
    assert rows == (log["symbol"] != "Cash").sum()
    history = pb.rebalance_history(run_id, url=stored)
    assert len(history) == log["rebalance_date"].nunique()
    assert {b["symbol"] for b in history[0]["buys"]} == set(log[log["rebalance_date"] == history[0]["date"]]["symbol"]) - {"Cash"}
    sells = [s for day in history for s in day["sells"]]
    assert sells and all(s["qty"] > 0 and s["price"] > 0 for s in sells)
    with pytest.raises(LookupError):
        pb.rebalance_history(9999, url=stored)


# Save and load a momentum strategy in the Strategy Library; the library opens it on page 8.
def test_strategy_library(stored):
    from marketlens import library as lib
    s = settings(top_n=3, hold_rank=4)
    sid = pb.save_momentum_strategy("Mom 3", s, notes="test", url=stored)
    assert pb.save_momentum_strategy("Mom 3", settings(top_n=4, hold_rank=4), url=stored) == sid   # same name = update
    assert pb.load_momentum_strategy(sid, url=stored).top_n == 4
    with pytest.raises(ValueError, match="name"):
        pb.save_momentum_strategy(" ", s, url=stored)
    strategy = lib.get_strategy(sid, url=stored)
    key, value, page = lib.handoff_for(strategy, "backtest")
    assert page == lib.PAGES["momentum"] and value["to"] == "momentum" and value["strategy_id"] == sid
