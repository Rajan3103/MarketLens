"""Tests for marketlens/momentum/etf_bt.py, the ETF cost segments and the price repair.

Prices are made up by hand, so every expected number can be worked out on paper.
The Strategy Library test uses TEST_DATABASE_URL (skipped without it).
"""
import copy
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from marketlens.costs import DP_CHARGE_RS, CostSettings, trade_costs
from marketlens.ingest.clean import repair_scale_errors
from marketlens.momentum import etf_bt as eb
from marketlens.momentum.etf_bt import (NO_COSTS, EtfBacktestSettings, EtfData, TaxBook, TaxSettings,
                                        rebalance_days, run_etf_backtest, year_tax)

DAYS = pd.bdate_range("2020-01-01", "2023-12-29")
CATS = {"UP": "Equity - Large Cap", "DOWN": "Equity - Sector", "GOLD": "Commodity - Gold",
        "NIFTYBEES": "Equity - Large Cap", "LIQUIDBEES": "Cash"}


# Prices that grow by a fixed % every day, one column per symbol.
def steady(rates: dict, days=DAYS) -> pd.DataFrame:
    n = np.arange(len(days))
    return pd.DataFrame({s: 100 * (1 + r) ** n for s, r in rates.items()}, index=days)


# Made-up ETF data with a benchmark and LIQUIDBEES.
def make_data(rates: dict, days=DAYS) -> EtfData:
    closes = steady({**rates, "NIFTYBEES": 0.0003, "LIQUIDBEES": 0.0}, days)
    return EtfData(closes.round(4), CATS)


# Settings without costs or tax (so results can be checked by hand), changed by **kw.
def free(**kw) -> EtfBacktestSettings:
    base = dict(symbols=["UP", "DOWN"], top_k=1, lookbacks=[63], weights=[1], start=dt.date(2021, 1, 1),
                capital=1_000_000, costs=NO_COSTS, tax=TaxSettings(enabled=False), drift_band_pct=10)
    return EtfBacktestSettings(**{**base, **kw})


# ---- costs.py: ETF segments ----

def test_etf_costs():
    buy = trade_costs("etf_equity", "BUY", 250, 400)             # Rs 1,00,000
    assert buy.brokerage == 0 and buy.stt == 0 and buy.dp == 0
    assert buy.stamp == 15.00                                      # 0.015% on BUY
    sell = trade_costs("etf_equity", "SELL", 250, 400)
    assert sell.stt == 1.00                                        # 0.001% on SELL (not 0.1% like shares)
    assert sell.dp == DP_CHARGE_RS
    assert sell.gst == pytest.approx(round((sell.exchange + sell.sebi + DP_CHARGE_RS) * 0.18, 2), abs=0.01)
    gold = trade_costs("etf_other", "SELL", 250, 400)
    assert gold.stt == 0 and gold.dp == DP_CHARGE_RS               # no STT on gold / international / liquid
    assert trade_costs("etf_equity", "SELL", 250, 400, CostSettings(dp_charge=0)).dp == 0
    assert trade_costs("equity_delivery", "SELL", 2500, 10).dp == DP_CHARGE_RS


# ---- price repair ----

def test_repair_scale_errors():
    closes = pd.DataFrame({"GLITCH": [33.5, 33.6, 0.34, 0.34, 33.65, 33.7],
                           "SPLIT": [500.0, 510, 51.2, 51.5, 52, 52.4],     # 1:10 split never adjusted
                           "REAL": [100.0, 120, 96, 100, 101, np.nan]}, index=DAYS[:6])
    fixed, others, fixes = repair_scale_errors(closes, {"open": closes * 1.001})
    assert fixed["GLITCH"].tolist() == pytest.approx([33.5, 33.6, 34, 34, 33.65, 33.7])
    assert fixed["SPLIT"].pct_change().abs().max() < 0.05                     # no fake -90% day
    assert fixed["REAL"].equals(closes["REAL"])                               # +20% / -20% are real moves
    assert others["open"]["GLITCH"].iloc[2] == pytest.approx(34.034)
    assert {(f["symbol"], f["factor"]) for f in fixes} == {("GLITCH", 100.0), ("SPLIT", 10.0)}


def test_repair_uses_only_past_prices():
    closes = pd.DataFrame({"A": [10.0, 10.1, 1.01, 10.2, 10.3]}, index=DAYS[:5])
    part, _, _ = repair_scale_errors(closes.iloc[:3])
    full, _, _ = repair_scale_errors(closes)
    assert part["A"].tolist() == full["A"].iloc[:3].tolist()


# ---- tax ----

def test_year_tax_by_hand():
    s = TaxSettings()
    tax, st, lt = year_tax({"st_equity": 100_000}, 0, 0, s)
    assert tax == 20_800.00 and st == lt == 0                     # 20% + 4% cess
    tax, _, _ = year_tax({"lt_equity": 200_000}, 0, 0, s)
    assert tax == 9_750.00                                         # (2L - 1.25L) x 12.5% x 1.04
    tax, _, _ = year_tax({"lt_equity": 100_000}, 0, 0, s)
    assert tax == 0                                                # inside the Rs 1.25 lakh exemption
    tax, _, _ = year_tax({"st_slab": 50_000, "lt_other": 40_000}, 0, 0, s)
    assert tax == round((15_000 + 5_000) * 1.04, 2)                # slab 30% and 12.5%


def test_loss_set_off_and_carry_forward():
    s = TaxSettings()
    # A short-term loss goes to the highest-taxed gain first (slab 30%), then 20%, then long-term.
    tax, st, lt = year_tax({"st_equity": 40_000, "st_slab": 30_000, "lt_other": 50_000, "lt_equity": -10_000},
                           carried_st=80_000, carried_lt=0, s=s)
    # long-term loss 10k: lt_other 50k -> 40k; short-term loss 80k: slab 30k -> 0, equity 40k -> 0,
    # the last 10k: lt_other 40k -> 30k. Tax = 30k x 12.5% x 1.04.
    assert tax == round(30_000 * 0.125 * 1.04, 2) and st == 0 and lt == 0
    # A long-term loss can never reduce a short-term gain: it is carried forward.
    tax, st, lt = year_tax({"st_equity": 10_000, "lt_equity": -30_000}, 0, 0, s)
    assert tax == 2_080.00 and lt == 30_000 and st == 0


def test_tax_book_refund_and_next_year():
    book = TaxBook(TaxSettings())
    book.add(dt.date(2024, 5, 1), "equity", 50_000, False)
    assert book.settle(dt.date(2024, 5, 1)) == 10_400.00
    book.add(dt.date(2024, 9, 1), "equity", -20_000, False)
    assert book.settle(dt.date(2024, 9, 1)) == -4_160.00          # tax paid earlier in the year comes back
    book.add(dt.date(2025, 3, 1), "equity", -60_000, False)       # same FY 2024-25: net -30,000
    assert book.settle(dt.date(2025, 3, 1)) == -6_240.00
    book.add(dt.date(2025, 4, 2), "other", 40_000, False)         # FY 2025-26 at slab 30%, less 30k loss
    assert book.settle(dt.date(2025, 4, 2)) == round(10_000 * 0.30 * 1.04, 2)
    assert list(book.table()["Financial year"]) == ["FY 2024-25", "FY 2025-26"]
    debt = TaxBook(TaxSettings())
    debt.add(dt.date(2024, 5, 1), "debt", 10_000, True)            # liquid ETF: slab even after 12 months
    assert debt.settle(dt.date(2024, 5, 1)) == 3_120.00


# ---- rebalance days ----

def test_monthly_rebalance_days():
    pairs = rebalance_days(DAYS, dt.date(2021, 1, 1), dt.date(2021, 4, 15), "monthly")
    assert pairs[0] == (pd.Timestamp("2020-12-31"), pd.Timestamp("2021-01-01"))   # invested on day one
    assert pairs[1:] == [(pd.Timestamp("2021-01-29"), pd.Timestamp("2021-02-01")),
                         (pd.Timestamp("2021-02-26"), pd.Timestamp("2021-03-01")),
                         (pd.Timestamp("2021-03-31"), pd.Timestamp("2021-04-01"))]
    assert all(trade > signal for signal, trade in pairs)          # always traded AFTER the signal


def test_weekly_and_unfinished_period():
    pairs = rebalance_days(DAYS, dt.date(2021, 1, 4), dt.date(2021, 1, 20), "weekly")
    assert [t.date() for _, t in pairs] == [dt.date(2021, 1, 4), dt.date(2021, 1, 11), dt.date(2021, 1, 18)]
    # data ending mid-month: the last day is not a month end, so no trade on it
    pairs = rebalance_days(DAYS[DAYS <= "2021-03-17"], dt.date(2021, 1, 1), dt.date(2021, 3, 17), "monthly")
    assert pairs[-1][0] == pd.Timestamp("2021-02-26")


# ---- the backtest ----

def test_holds_the_riser_value_by_hand():
    data = make_data({"UP": 0.001, "DOWN": -0.001})
    result = run_etf_backtest(data, free())
    first = round(data.closes.loc["2021-01-01", "UP"], 2)              # fills are in paise (tick Rs 0.01)
    units = math.floor(1_000_000 / first)
    expected = units * data.closes["UP"].iloc[-1] + (1_000_000 - units * first)
    assert result.extras["final_value"] == pytest.approx(expected, abs=0.02)
    assert result.extras["holdings"]["ETF"].tolist() == ["UP", "Cash"]
    assert result.extras["orders"] == 1 and result.trades == []       # bought once, never sold


def test_costs_and_tax_are_taken_out():
    data = make_data({"UP": 0.001, "DOWN": -0.001})
    s = free(rebalance="weekly", drift_band_pct=0, costs=CostSettings(slippage_value=0.1))
    with_costs = run_etf_backtest(data, s)
    no_costs = run_etf_backtest(data, free(rebalance="weekly", drift_band_pct=0))
    assert with_costs.extras["charges"] > 0 and with_costs.extras["slippage"] > 0
    assert with_costs.extras["final_value"] < no_costs.extras["final_value"]
    taxed = run_etf_backtest(data, free(tax=TaxSettings(), costs=NO_COSTS))
    assert taxed.extras["tax_paid"] == 0                           # never sold, so no tax yet ...
    liq = taxed.extras["liquidation"]
    assert liq["exit_tax"] > 0 and liq["value"] < taxed.extras["final_value"]   # ... until it is sold


def test_rotation_trades_and_tax():
    """UP leads in 2021, DOWN (rising fast) leads from 2022: the ETF is sold and tax is paid."""
    n = np.arange(len(DAYS))
    turn = int((DAYS < "2022-01-01").sum())
    up = 100 * 1.001 ** n
    down = np.where(n < turn, 100 * 0.999 ** n, 100 * 0.999 ** turn * 1.004 ** (n - turn))
    closes = pd.DataFrame({"UP": up, "DOWN": down, "NIFTYBEES": up, "LIQUIDBEES": 1000.0}, index=DAYS)
    data = EtfData(closes, CATS)
    result = run_etf_backtest(data, free(tax=TaxSettings(), costs=CostSettings()))
    sold = [t for t in result.trades if t.symbol == "UP"]
    assert sold and sold[0].exit_reason == "rotated out" and sold[0].note == "long-term"   # held > 12 months
    assert result.extras["rebalances"]["bought"].str.contains("DOWN").any()
    gain = sum(t.gross_pnl - t.costs for t in sold)
    fy = result.extras["tax_by_year"]
    assert fy["Long-term equity (Rs)"].sum() == pytest.approx(gain, rel=0.01)


def test_no_look_ahead():
    """Changing prices after a day changes nothing up to that day (signals use closes up to the
    signal day; trades fill at the next close)."""
    data = make_data({"UP": 0.001, "DOWN": -0.0005, "GOLD": 0.0007})
    s = free(symbols=["UP", "DOWN", "GOLD"], top_k=2, costs=CostSettings(slippage_value=0.05), tax=TaxSettings())
    cut = pd.Timestamp("2022-06-15")
    before = run_etf_backtest(data, s)
    wild = copy.deepcopy(data)
    later = wild.closes.index > cut
    wild.closes.loc[later, ["UP", "GOLD"]] *= np.random.default_rng(3).uniform(0.2, 5, (later.sum(), 2))
    after = run_etf_backtest(wild, s)
    upto = before.equity.index <= cut.date()
    pd.testing.assert_series_equal(before.equity[upto], after.equity[upto])
    logs = [r.extras["rebalances"] for r in (before, after)]
    early = [log[log["trade_day"] <= cut.date()].reset_index(drop=True) for log in logs]
    pd.testing.assert_frame_equal(*early)


def test_signal_day_close_not_trade_day_close():
    """The pick is made from the signal day's close even if the trade day's close would change it."""
    data = make_data({"UP": 0.001, "DOWN": 0.0005})
    jump = data.closes.index.get_loc(pd.Timestamp("2021-02-01"))      # a trade day (after 29-Jan signal)
    data.closes.iloc[jump:, data.closes.columns.get_loc("DOWN")] *= 3  # DOWN leaps ON the trade day
    log = run_etf_backtest(data, free()).extras["rebalances"]
    row = log[log["trade_day"] == dt.date(2021, 2, 1)].iloc[0]
    assert row["target"].startswith("UP")                              # the signal had not seen the leap


def test_absolute_filter_goes_to_liquidbees_or_cash():
    data = make_data({"UP": -0.0005, "DOWN": -0.001})                 # everything falls
    liquid = run_etf_backtest(data, free(liquid_yield_pct=6.0))
    assert liquid.extras["holdings"]["ETF"].tolist() == ["LIQUIDBEES", "Cash"]
    years = (liquid.end - DAYS[0].date()).days / 365 - (liquid.start - DAYS[0].date()).days / 365
    assert liquid.extras["final_value"] == pytest.approx(1_000_000 * 1.06 ** years, rel=0.002)
    cash = run_etf_backtest(data, free(safe_asset="CASH"))
    assert cash.extras["final_value"] == 1_000_000 and cash.extras["orders"] == 0
    off = run_etf_backtest(data, free(abs_filter=False))               # filter off: hold the best faller
    assert off.extras["holdings"]["ETF"].iloc[0] == "UP"


def test_late_listed_etf_waits_for_history():
    closes = steady({"UP": 0.0005, "NEW": 0.003, "NIFTYBEES": 0.0003, "LIQUIDBEES": 0.0})
    closes.loc[:"2021-06-30", "NEW"] = np.nan                         # listed in July 2021
    data = EtfData(closes, {**CATS, "NEW": "International"})
    log = run_etf_backtest(data, free(symbols=["UP", "NEW"])).extras["rebalances"]
    held_new = log[log["target"].str.contains("NEW")]
    first = held_new["signal_day"].min()
    listed = closes["NEW"].first_valid_index()
    assert len(closes.loc[listed:pd.Timestamp(first)]) >= 64           # 63-day lookback + the start day
    assert (log[log["signal_day"] < first]["target"].str.startswith("UP")).all()


def test_check_claim_and_sweep():
    data = make_data({"UP": 0.001, "DOWN": -0.001, "GOLD": 0.0006})
    s = free(symbols=["UP", "DOWN", "GOLD"], rebalance="weekly", costs=CostSettings(slippage_value=0.05),
             tax=TaxSettings(), drift_band_pct=0)
    out = eb.check_claim(data, s, 670.0, dt.date(2021, 1, 1), dt.date(2023, 12, 29))
    table = out["table"].set_index("Line")
    assert table.loc["Claim", "CAGR %"] == pytest.approx(((7.7 ** (365 / 1092)) - 1) * 100, abs=0.01)
    before = table.loc["My backtest: before costs and tax", "Total return %"]
    after = table.loc["My backtest: after costs", "Total return %"]
    assert before > after and out["steps"]["costs_points"] == pytest.approx(before - after, abs=0.01)
    assert "Nifty 50 (NIFTYBEES)" in table.index
    sweep = eb.parameter_sweep(data, free(symbols=["UP", "DOWN", "GOLD"]), ks=range(1, 3), lookback_sets=[[21], [63]])
    assert len(sweep) == 4 and set(sweep["Top K"]) == {1, 2}


def test_results_tables():
    data = make_data({"UP": 0.001, "DOWN": -0.001})
    result = run_etf_backtest(data, free())
    comp = eb.comparison_table(result).set_index("Measure")
    assert comp.loc["CAGR %", "Strategy"] > comp.loc["CAGR %", "Nifty 50 (NIFTYBEES)"]
    years = eb.yearly_table(result)
    assert years["Year"].tolist() == [2021, 2022, 2023]
    growth = (1 + years["Strategy %"] / 100).prod() * 1_000_000
    assert growth == pytest.approx(result.extras["final_value"], rel=1e-3)
    table, weights = eb.current_signal(data, free())
    assert weights == {"UP": 1.0} and table.iloc[0]["symbol"] == "UP"


@pytest.mark.parametrize("bad", [dict(top_k=6), dict(rebalance="daily"), dict(safe_asset="GOLD"),
                                 dict(symbols=["LIQUIDBEES"]), dict(capital=100), dict(end=dt.date(2020, 1, 1))])
def test_bad_settings(bad):
    with pytest.raises(ValueError):
        free(**bad).check()


def test_params_round_trip():
    s = free(costs=CostSettings(slippage_value=0.07), tax=TaxSettings(slab_pct=20), end=dt.date(2023, 6, 30))
    back = EtfBacktestSettings.from_params(s.to_params())
    assert back == s


def test_demo_data_runs():
    data = eb.demo_etf_data(start=dt.date(2019, 1, 1), end=dt.date(2022, 12, 30))
    assert data.is_demo
    result = run_etf_backtest(data, EtfBacktestSettings(symbols=list(data.closes.columns), start=dt.date(2020, 6, 1)))
    assert result.is_demo and result.extras["final_value"] > 0


def test_save_to_strategy_library(db_url, db_session):
    s = free(end=dt.date(2023, 6, 30))
    sid = eb.save_etf_strategy("ETF top 1", s, url=db_url)
    assert eb.save_etf_strategy("ETF top 1", free(top_k=2), url=db_url) == sid        # same name = update
    assert eb.load_etf_strategy(sid, url=db_url).top_k == 2
    with pytest.raises(ValueError):
        eb.save_etf_strategy("  ", s, url=db_url)


def test_first_etf_day_reported():
    closes = steady({"UP": 0.001, "NIFTYBEES": 0.0003, "LIQUIDBEES": 0.0})
    data = EtfData(closes, CATS)
    early = run_etf_backtest(data, free(symbols=["UP"], lookbacks=[252], start=dt.date(2020, 2, 1)))
    assert early.extras["first_etf_day"] == dt.date(2021, 1, 1)        # 252 trading days after 1-Jan-2020 + month end
    assert early.extras["rebalances"]["target"].iloc[0].startswith("LIQUIDBEES")
