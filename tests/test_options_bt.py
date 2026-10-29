"""Tests for marketlens/backtest/options_bt.py.

The hand-made day (Monday 5-Jan-2026, NIFTY weekly expiry Tuesday 6-Jan-2026), spot 26,010:

  candle   CE 26000   PE 25900       Entry 09:17 uses the 09:16 candle (the last one finished)
  09:15      100         40          Exit  09:20 uses the 09:19 candle
  09:16      102         41
  09:17      104         42          Leg 1: SELL CE ATM (26000):  sold 102, bought back 95 -> +7 x 65 = +455
  09:18       99         43          Leg 2: BUY  PE ATM-1 (25900): bought 41, sold 45     -> +4 x 65 = +260
  09:19       95         45
  09:20+     500        500  <- huge prices AFTER the exit decision: must never be used
"""
import datetime as dt
import time

import numpy as np
import pandas as pd
import pytest

from marketlens.backtest.engine import order_costs
from marketlens.backtest.options_bt import (BARS, DayArrays, LegSpec, OptionsBacktestSettings, choose_expiry,
                                            fill_price, run_options_backtest)
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings

DAY, EXPIRY = dt.date(2026, 1, 5), dt.date(2026, 1, 6)
NO_COSTS = CostSettings(brokerage_per_order=0, include_taxes=False)
CE = {26000: [100, 102, 104, 99, 95], 26100: [60, 61, 62, 60, 58], 25900: [160, 161, 163, 158, 150]}
PE = {25900: [40, 41, 42, 43, 45], 26000: [80, 81, 79, 84, 88], 26100: [130, 131, 129, 135, 140]}


# One day as load_option_day would return it: candles 09:15.. with closes from CE / PE, then
# `after` for every candle from 09:20 to 09:29.
def hand_day(day=DAY, after: float = 500.0, spot: float | None = 26010.0) -> pd.DataFrame:
    rows = []
    for kind, table in (("CE", CE), ("PE", PE)):
        for strike, closes in table.items():
            for m, close in enumerate(closes + [after] * 10):
                ts = dt.datetime.combine(day, dt.time(9, 15), IST) + dt.timedelta(minutes=m)
                rows.append({"ts": ts, "strike": float(strike), "option_type": kind, "open": close, "high": close,
                             "low": close, "close": float(close), "volume": 1, "oi": 1, "iv": 14.0,
                             "spot": spot, "is_demo": False})
    return pd.DataFrame(rows)


class FrameData:
    """A fake data source: {(day, expiry): table} instead of PostgreSQL."""

    def __init__(self, frames: dict, extra_days=()):
        self.frames, self.extra = frames, list(extra_days)
        self.loaded = []

    def trading_days(self, start, end):
        return sorted({d for d, _ in self.frames} | set(self.extra))

    def load_day(self, day, expiry):
        self.loaded.append((day, expiry))
        return self.frames.get((day, expiry), pd.DataFrame())


# The straddle-like settings used by most tests.
def settings(legs=None, costs=NO_COSTS, **kw) -> OptionsBacktestSettings:
    legs = legs or [LegSpec("SELL", "CE"), LegSpec("BUY", "PE", strike_rule="ATM", value=-1)]
    return OptionsBacktestSettings("NIFTY", DAY, DAY, legs, entry_time=dt.time(9, 17),
                                   exit_time=dt.time(9, 20), costs=costs, capital=100_000, **kw)


# Run on the hand-made day without saving.
def run(s=None, frame=None, **kw):
    return run_options_backtest(s or settings(), FrameData({(DAY, EXPIRY): hand_day() if frame is None else frame}),
                                save=False, **kw)


# ---- P&L checked by hand ----

def test_hand_pnl_without_costs():
    r = run()
    ce, pe = r.trades
    assert (ce.symbol, ce.side, ce.qty, ce.entry_price, ce.exit_price) == ("NIFTY 26000 CE 06-Jan-2026", "SELL", 65, 102, 95)
    assert (pe.symbol, pe.side, pe.entry_price, pe.exit_price) == ("NIFTY 25900 PE 06-Jan-2026", "BUY", 41, 45)
    assert (ce.gross_pnl, pe.gross_pnl) == (455, 260) and r.metrics["total_pnl"] == 715
    assert ce.entry_time == dt.datetime(2026, 1, 5, 9, 17, tzinfo=IST) and ce.exit_reason == "TIME"


# Mark to market each minute 09:17..09:20 (candles 09:16..09:19):
#   CE: -(102,104,99,95 - 102) x 65 = 0, -130, 195, 455;  PE: (41,42,43,45 - 41) x 65 = 0, 65, 130, 260
def test_hand_mtm():
    mtm = run().extras["mtm"].iloc[0]
    assert (mtm["max_profit"], mtm["max_profit_at"]) == (715, dt.time(9, 20))
    assert (mtm["max_loss"], mtm["max_loss_at"]) == (-65, dt.time(9, 18))
    assert mtm["spot_at_entry"] == 26010


# Slippage of Rs 1: sell 101 / buy back 96 (+325); buy 42 / sell 44 (+130).
def test_hand_slippage_and_costs():
    slip = CostSettings(slippage_type="points", slippage_value=1)
    ce, pe = run(settings(costs=slip)).trades
    assert (ce.entry_price, ce.exit_price, ce.gross_pnl) == (101, 96, 325)
    assert (pe.entry_price, pe.exit_price, pe.gross_pnl) == (42, 44, 130)
    # Brokerage and taxes come from costs.py (slippage is not counted twice)
    assert ce.costs == order_costs("options", "SELL", 101, 96, 65, CostSettings())
    assert ce.pnl == pytest.approx(325 - ce.costs)
    assert fill_price(10, "BUY", CostSettings(slippage_value=1)) == 10.1          # 1% of 10
    assert fill_price(0.05, "SELL", slip) == 0.05                                  # never below 5 paise


# Strike rules: premium ~60 -> CE 26100 (61 at 09:16); delta ~0.5 -> the ATM put; more lots.
def test_strike_rules():
    legs = [LegSpec("SELL", "CE", 2, "PREMIUM", 60), LegSpec("SELL", "PE", 1, "DELTA", 0.5),
            LegSpec("BUY", "CE", 1, "ATM", 1)]
    t = run(settings(legs)).trades
    assert [x.symbol.split()[1] for x in t] == ["26100", "26000", "26100"]
    assert t[0].qty == 130 and t[0].entry_price == 61


# Without stored index minutes the spot comes from put-call parity: 26000 + 102 - 81 = 26021.
def test_spot_from_parity():
    arrays = DayArrays.from_frame(hand_day(spot=None), DAY, EXPIRY)
    assert arrays.spot_at(dt.time(9, 17)) == 26021
    assert run(frame=hand_day(spot=None)).trades[0].symbol.startswith("NIFTY 26000 CE")


# ---- no look-ahead ----

# Changing every candle from the entry minute onwards must not change the strikes or entry prices,
# and changing candles from the exit minute onwards must not change anything at all.
def test_no_look_ahead():
    base = run()
    entry_cut = dt.datetime.combine(DAY, dt.time(9, 17), IST)
    exit_cut = dt.datetime.combine(DAY, dt.time(9, 20), IST)
    for cut, everything in ((entry_cut, False), (exit_cut, True)):
        frame = hand_day()
        later = frame["ts"] >= cut
        frame.loc[later, "close"] = np.random.default_rng(1).uniform(1, 1000, later.sum()).round(1)
        frame.loc[later, "spot"] = 30_000.0
        other = run(frame=frame)
        assert [(t.symbol, t.entry_price) for t in other.trades] == [(t.symbol, t.entry_price) for t in base.trades]
        if everything:
            assert [t.pnl for t in other.trades] == [t.pnl for t in base.trades]
            assert other.extras["mtm"].equals(base.extras["mtm"])


# Each minute's "known" values equal those from a table cut just before that minute.
def test_known_values_use_only_finished_candles():
    full = DayArrays.from_frame(hand_day(), DAY, EXPIRY)
    for minute in (dt.time(9, 16), dt.time(9, 18), dt.time(9, 21)):
        cut = hand_day()
        cut = cut[cut["ts"] < dt.datetime.combine(DAY, minute, IST)]
        part = DayArrays.from_frame(cut, DAY, EXPIRY)
        pd.testing.assert_frame_equal(full.options_at(minute), part.options_at(minute))
        assert full.spot_at(minute) == part.spot_at(minute)
    with pytest.raises(ValueError):
        full.options_at(dt.time(9, 15))          # nothing has finished at 09:15


# ---- days, expiries, skipping ----

def test_expiry_choice():
    s = settings()
    assert choose_expiry(s, DAY, set()) == EXPIRY                                       # Tue 6-Jan
    assert choose_expiry(settings(expiry_which="next"), DAY, set()) == dt.date(2026, 1, 13)
    assert choose_expiry(settings(expiry_type="monthly"), DAY, set()) == dt.date(2026, 1, 27)
    # Expiry Tue 27-Jan-2026 as a holiday (no data) -> moved to Mon 26-Jan
    days = {dt.date(2026, 1, 23), dt.date(2026, 1, 26), dt.date(2026, 1, 28)}
    assert choose_expiry(s, dt.date(2026, 1, 23), days) == dt.date(2026, 1, 26)
    # On Wed 28-Jan the next weekly expiry is Tue 3-Feb
    assert choose_expiry(s, dt.date(2026, 1, 28), days) == dt.date(2026, 2, 3)


# Weekday filter, skip-expiry-day and a day without data are all reported.
def test_skipping():
    tue = dt.date(2026, 1, 6)
    frames = {(DAY, EXPIRY): hand_day(), (tue, EXPIRY): hand_day(tue)}
    s = OptionsBacktestSettings("NIFTY", DAY, dt.date(2026, 1, 7), settings().legs, entry_time=dt.time(9, 17),
                                exit_time=dt.time(9, 20), costs=NO_COSTS, skip_expiry_day=True)
    r = run_options_backtest(s, FrameData(frames, [dt.date(2026, 1, 7)]), save=False)
    assert r.extras["days_traded"] == 1 and len(r.trades) == 2
    reasons = dict(zip(r.extras["skipped"]["date"], r.extras["skipped"]["reason"]))
    assert "expiry day" in reasons[tue] and "no stored data" in reasons[dt.date(2026, 1, 7)]
    only_tuesday = OptionsBacktestSettings("NIFTY", DAY, tue, settings().legs, entry_time=dt.time(9, 17),
                                           exit_time=dt.time(9, 20), costs=NO_COSTS, weekdays=(1,))
    data = FrameData(frames)
    assert len(run_options_backtest(only_tuesday, data, save=False).trades) == 2 and data.loaded == [(tue, EXPIRY)]


# Bad settings are refused.
@pytest.mark.parametrize("kw", [{"legs": []}, {"entry_time": dt.time(9, 15)}, {"exit_time": dt.time(9, 16)},
                                {"weekdays": (5,)}, {"expiry_type": "yearly"}])
def test_settings_checks(kw):
    base = dict(underlying="NIFTY", start=DAY, end=DAY, legs=[LegSpec("SELL", "CE")],
                entry_time=dt.time(9, 17), exit_time=dt.time(9, 20))
    with pytest.raises(ValueError):
        OptionsBacktestSettings(**{**base, **kw})
    with pytest.raises(ValueError):
        LegSpec("SELL", "XX")
    with pytest.raises(ValueError):
        LegSpec("SELL", "CE", strike_rule="PREMIUM")       # needs a premium
    s = settings()
    assert s.lot_size == 65 and s.to_params()["legs"][1]["value"] == -1 and "SELL 1x CE ATM" in s.name


# ---- saving ----

def test_saved_with_save_run(db_url, db_session):
    from marketlens.backtest.store import load_run
    r = run_options_backtest(settings(), FrameData({(DAY, EXPIRY): hand_day()}), url=db_url)
    back = load_run(r.extras["run_id"], url=db_url)
    assert [t.pnl for t in back.trades] == [t.pnl for t in r.trades]
    assert back.params["entry_time"] == "09:17" and back.params["legs"][0]["side"] == "SELL"


# ---- speed: one year of NIFTY minutes, 4 legs, under 60 seconds ----

class YearOfMinutes:
    """250 trading days of made-up NIFTY minutes, 21 strikes x CE/PE x 375 candles (like Dhan's ATM +/- 10)."""

    def __init__(self):
        self.days = [d.date() for d in pd.bdate_range("2025-01-01", "2025-12-31")][:250]
        self.rng = np.random.default_rng(3)
        # 5 different days, made once and reused (moved to each date), so the test times the backtester
        self.templates = [self.make_day(self.days[0]) for _ in range(5)]

    def trading_days(self, start, end):
        return [d for d in self.days if start <= d <= end]

    def load_day(self, day, expiry):
        frame = self.templates[self.days.index(day) % 5].copy()
        frame["ts"] = frame["ts"] + pd.Timedelta(days=(day - self.days[0]).days)
        return frame

    def make_day(self, day):
        spot = 23_500 + self.rng.normal(0, 30, BARS).cumsum()
        strikes = np.round(spot[0] / 50) * 50 + 50 * np.arange(-10, 11)
        ts = pd.date_range(dt.datetime.combine(day, dt.time(9, 15)), periods=BARS, freq="min", tz=IST)
        k, s = np.meshgrid(strikes, spot)                                   # (minutes, strikes)
        ce = np.maximum(s - k, 0) + 80 * np.exp(-np.abs(s - k) / 300) + 1
        pe = np.maximum(k - s, 0) + 80 * np.exp(-np.abs(s - k) / 300) + 1
        n = len(strikes)
        close = np.concatenate([ce.ravel(), pe.ravel()]).round(2)
        wiggle = self.rng.uniform(0, 0.03, close.size) * close
        return pd.DataFrame({
            "ts": np.tile(np.repeat(ts, n), 2), "strike": np.tile(strikes, 2 * BARS),
            "option_type": np.repeat(["CE", "PE"], BARS * n), "open": close, "high": close + wiggle,
            "low": close - wiggle, "close": close, "volume": 1, "oi": 1, "iv": 14.0,
            "spot": np.tile(np.repeat(spot, n), 2), "is_demo": True})


def test_one_year_four_legs_under_60_seconds():
    # Every rule switched on: leg SL / target / trailing, re-entry, SL to cost and the overall M2M
    legs = [LegSpec("SELL", "CE", sl=30, trail_x=10, trail_y=5, reentry="reexecute", max_reentries=2),
            LegSpec("SELL", "PE", sl=30, target=50, reentry="cost", max_reentries=1),
            LegSpec("BUY", "CE", strike_rule="DELTA", value=0.2),
            LegSpec("BUY", "PE", strike_rule="PREMIUM", value=20)]
    s = OptionsBacktestSettings("NIFTY", dt.date(2025, 1, 1), dt.date(2025, 12, 31), legs, move_sl_to_cost=True,
                                m2m_sl=20_000, m2m_lock_at=5_000, m2m_lock_to=2_000, m2m_trail_x=1_000, m2m_trail_y=500)
    started = time.perf_counter()
    r = run_options_backtest(s, YearOfMinutes(), save=False)
    seconds = time.perf_counter() - started
    assert r.extras["days_traded"] == 250 and len(r.trades) >= 1000 and r.extras["skipped"].empty
    assert seconds < 60, f"took {seconds:.1f} s"
