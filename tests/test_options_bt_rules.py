"""Tests for the exit and entry rules in marketlens/backtest/options_bt.py.

Every test uses a tiny hand-made day (Monday 5-Jan-2026, expiry Tuesday 6-Jan-2026), lot size 10,
no costs, entry 09:17 and exit 09:25, so each P&L can be checked by hand.

Candle numbers: 0 = 09:15, 1 = 09:16, 2 = 09:17, 3 = 09:18, 4 = 09:19, 5 = 09:20 ... 9 = 09:24.
The entry at 09:17 uses the close of candle 1 (09:16). The time exit at 09:25 uses the close of candle 9.
A candle is a number (open = high = low = close) or (open, high, low, close).
"""
import datetime as dt

import pandas as pd
import pytest

from marketlens.backtest.options_bt import BOTH_HIT, LegSpec, OptionsBacktestSettings, run_options_backtest
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings

DAY, EXPIRY = dt.date(2026, 1, 5), dt.date(2026, 1, 6)
NO_COSTS = CostSettings(brokerage_per_order=0, include_taxes=False)
STRIKES = (25900.0, 26000.0, 26100.0)
N = 14          # candles 09:15 .. 09:28


# Clock time of candle m.
def t(m: int) -> dt.datetime:
    return dt.datetime.combine(DAY, dt.time(9, 15), IST) + dt.timedelta(minutes=m)


# A day of candles. series: {("CE", 26000): [candles...]}; every other strike is flat at 100.
# A short list keeps its last close for the remaining minutes. spot: one number or one per minute.
def day_frame(series: dict, spot=26010.0) -> pd.DataFrame:
    rows = []
    spots = spot if isinstance(spot, list) else [spot] * N
    spots = spots + [spots[-1]] * (N - len(spots))
    for kind in ("CE", "PE"):
        for strike in STRIKES:
            bars = list(series.get((kind, strike), [100]))
            last = bars[-1] if not isinstance(bars[-1], tuple) else bars[-1][3]
            bars += [last] * (N - len(bars))
            for m, bar in enumerate(bars):
                o, h, low, c = bar if isinstance(bar, tuple) else (bar,) * 4
                rows.append({"ts": t(m), "strike": strike, "option_type": kind, "open": o, "high": h, "low": low,
                             "close": c, "volume": 1, "oi": 1, "iv": 14.0, "spot": spots[m], "is_demo": False})
    return pd.DataFrame(rows)


class OneDay:
    """A fake data source holding one day."""

    def __init__(self, frame):
        self.frame = frame

    def trading_days(self, start, end):
        return [DAY]

    def load_day(self, day, expiry):
        return self.frame


# Settings with entry 09:17, exit 09:25, lot size 10 and no costs.
def settings(legs, **kw) -> OptionsBacktestSettings:
    base = dict(entry_time=dt.time(9, 17), exit_time=dt.time(9, 25), costs=NO_COSTS, lot_size=10, capital=100_000)
    return OptionsBacktestSettings("NIFTY", DAY, DAY, legs, **{**base, **kw})


# Run one day and return the trades as a table.
def run(legs, series, spot=26010.0, **kw):
    result = run_options_backtest(settings(legs, **kw), OneDay(day_frame(series, spot)), save=False)
    return result, result.trades_frame()


CE, PE = ("CE", 26000.0), ("PE", 26000.0)


# ---- 1. stop loss and target (checked on each minute's high / low) ----

# Sold at 100, SL 20% = 120. Candle 3 reaches 125 -> out at 120 at 09:18: (100 - 120) x 10 = -200.
def test_sl_percent():
    _, tr = run([LegSpec("SELL", "CE", sl=20)], {CE: [100, 100, (100, 110, 95, 105), (105, 125, 104, 118)]})
    row = tr.iloc[0]
    assert (row.exit_price, row.exit_reason, row.exit_time, row.gross_pnl) == (120, "SL", t(3), -200)
    assert row.note is None


# Bought PE at 50, target 30 points = 80. Candle 4 reaches 82 -> out at 80: +300.
def test_target_points():
    _, tr = run([LegSpec("BUY", "PE", unit="points", target=30)], {PE: [50, 50, 55, (55, 70, 54, 65), (65, 82, 64, 70)]})
    row = tr.iloc[0]
    assert (row.entry_price, row.exit_price, row.exit_reason, row.exit_time, row.gross_pnl) == (50, 80, "TARGET", t(4), 300)


# SL in Rs on the whole leg: Rs 150 / 10 units = 15 points -> SL 115.
def test_sl_rupees():
    _, tr = run([LegSpec("SELL", "CE", unit="rs", sl=150)], {CE: [100, 100, (100, 116, 99, 110)]})
    assert (tr.iloc[0].exit_price, tr.iloc[0].gross_pnl) == (115, -150)


# Both the SL (120) and the target (70) inside one candle: assume the SL (worse) and say so.
def test_both_hit_assumes_sl():
    _, tr = run([LegSpec("SELL", "CE", unit="points", sl=20, target=30)], {CE: [100, 100, (100, 121, 69, 100)]})
    row = tr.iloc[0]
    assert (row.exit_price, row.exit_reason, row.note) == (120, "SL", BOTH_HIT)


# The price opens above the SL (gap): out at the open, 130, not at 120.
def test_gap_past_sl():
    _, tr = run([LegSpec("SELL", "CE", unit="points", sl=20)], {CE: [100, 100, (130, 135, 128, 131)]})
    assert tr.iloc[0].exit_price == 130 and "opened past the SL" in tr.iloc[0].note


# Nothing hit: out at the exit time at candle 9's close.
def test_time_exit():
    _, tr = run([LegSpec("SELL", "CE", sl=50)], {CE: [100, 100, 98, 97, 96, 95, 94, 93, 92, 91, 500]})
    row = tr.iloc[0]
    assert (row.exit_price, row.exit_reason, row.exit_time) == (91, "TIME", dt.datetime.combine(DAY, dt.time(9, 25), IST))


# ---- 2. trailing stop loss and lock profit ----

# Sold at 100, SL 120, trail every 10 points by 5:
#   candle 2 low 85 -> 15 in favour -> 1 step -> SL 115;  candle 3 low 79 -> 21 -> 2 steps -> SL 110
#   candle 4 high 111 -> out at 110 (TRAIL_SL): (100 - 110) x 10 = -100
def test_trailing_sl():
    _, tr = run([LegSpec("SELL", "CE", unit="points", sl=20, trail_x=10, trail_y=5)],
                {CE: [100, 100, (100, 101, 85, 90), (90, 92, 79, 85), (85, 111, 84, 105)]})
    row = tr.iloc[0]
    assert (row.exit_price, row.exit_reason, row.exit_time, row.gross_pnl) == (110, "TRAIL_SL", t(4), -100)


# The SL moves only with what is known: the minute that made the new low is not checked against the new SL.
def test_trailing_uses_next_minute():
    _, tr = run([LegSpec("SELL", "CE", unit="points", sl=20, trail_x=10, trail_y=5)],
                {CE: [100, 100, (100, 116, 85, 90)]})       # low 85 and high 116 in the same minute
    assert tr.iloc[0].exit_reason == "TIME"                  # 116 < 120 (the SL during that minute)


# Bought at 100, SL 90, lock: once 20 in favour, lock 5 profit -> SL 105. Candle 3 dips to 104 -> out at 105.
def test_lock_profit():
    _, tr = run([LegSpec("BUY", "CE", unit="points", sl=10, lock_at=20, lock_to=5)],
                {CE: [100, 100, (100, 121, 99, 115), (115, 116, 104, 108)]})
    row = tr.iloc[0]
    assert (row.exit_price, row.exit_reason, row.gross_pnl) == (105, "TRAIL_SL", 50)


# ---- 3. re-entry after a stop loss ----

# Re-entry at cost: SL at 110 in candle 2; candle 4 comes back to 100 -> sold again at 100 (09:19);
# then out at the exit time at 101.  Trades: -100 and -10.
def test_reentry_at_cost():
    _, tr = run([LegSpec("SELL", "CE", unit="points", sl=10, reentry="cost", max_reentries=1)],
                {CE: [100, 100, (100, 112, 99, 111), (111, 113, 105, 106), (106, 107, 99, 101), 101]})
    assert list(tr.exit_reason) == ["SL", "TIME"] and list(tr.gross_pnl) == [-100, -10]
    second = tr.iloc[1]
    assert (second.entry_price, second.entry_time, second.note) == (100, t(4), "re-entry 1 at cost")


# Re-execute: SL in candle 2, the spot has moved to 26,110 -> at 09:18 sell the new ATM, 26100 CE,
# at candle 2's close (60).
def test_reexecute_new_strike():
    _, tr = run([LegSpec("SELL", "CE", unit="points", sl=10, reentry="reexecute", max_reentries=1)],
                {CE: [100, 100, (100, 115, 99, 114)], ("CE", 26100.0): [60]},
                spot=[26010, 26010, 26110])
    assert list(tr.symbol) == ["NIFTY 26000 CE 06-Jan-2026", "NIFTY 26100 CE 06-Jan-2026"]
    second = tr.iloc[1]
    assert (second.entry_price, second.entry_time, second.exit_reason) == (60, t(3), "TIME")
    assert second.note == "re-execute 1 with a new strike"


# At most max_reentries: price keeps rising, SL 5 points -> 1 entry + 2 re-entries, each -50.
def test_max_reentries():
    rising = [100, 100, (100, 106, 100, 106), (106, 112, 106, 112), (112, 118, 112, 118), (118, 124, 118, 124)]
    _, tr = run([LegSpec("SELL", "CE", unit="points", sl=5, reentry="reexecute", max_reentries=2)], {CE: rising})
    assert list(tr.entry_price) == [100, 106, 112] and list(tr.gross_pnl) == [-50, -50, -50]
    assert list(tr.exit_reason) == ["SL", "SL", "SL"]


# Move the other legs' SL to cost: CE stops out in candle 2; the PE's SL moves from 130 to its entry 80,
# and candle 3 touches 81 -> PE out at 80 (COST_SL, P&L 0).
def test_move_sl_to_cost():
    _, tr = run([LegSpec("SELL", "CE", unit="points", sl=10), LegSpec("SELL", "PE", unit="points", sl=50)],
                {CE: [100, 100, (100, 111, 99, 105)], PE: [80, 80, (80, 80, 70, 75), (75, 81, 74, 78)]},
                move_sl_to_cost=True)
    pe = tr[tr.leg_no == 2].iloc[0]
    assert (pe.exit_price, pe.exit_reason, pe.exit_time, pe.gross_pnl) == (80, "COST_SL", t(3), 0)


# ---- 4. range breakout entry ----

# Spot range 09:15-09:18 (candles 0-2): high 26,020, low 25,990. Candle 4 closes at 26,030 (above):
# enter at 09:20 with candle 4's prices.
def test_underlying_breakout():
    spots = [26000, 26020, 25990, 26010, 26030, 26030]
    result, tr = run([LegSpec("SELL", "CE")], {CE: [100, 100, 100, 100, 97, 95]}, spot=spots,
                     entry_mode="breakout", range_end=dt.time(9, 18), breakout_side="high")
    row = tr.iloc[0]
    assert (row.entry_time, row.entry_price) == (t(5), 97) and "above the range high" in row.note
    assert result.extras["mtm"].iloc[0]["entered_at"] == dt.time(9, 20)
    # Only a break of the LOW counts -> no trade, and the day is listed as skipped
    result, tr = run([LegSpec("SELL", "CE")], {CE: [100]}, spot=spots,
                     entry_mode="breakout", range_end=dt.time(9, 18), breakout_side="low")
    assert tr.empty and "No breakout" in result.extras["skipped"].iloc[0]["reason"]


# Leg range 09:15-09:18: highs 102, 104, 103 -> 104. Candle 4 reaches 108 -> bought at 104 at 09:19.
def test_leg_breakout():
    bars = [(100, 102, 98, 101), (101, 104, 97, 103), (103, 103, 99, 100), (100, 104, 100, 101),
            (102, 108, 101, 107), 110]
    _, tr = run([LegSpec("BUY", "CE")], {CE: bars}, entry_mode="breakout", range_end=dt.time(9, 18),
                range_source="leg", breakout_side="high")
    row = tr.iloc[0]
    assert (row.entry_price, row.entry_time, row.exit_price, row.gross_pnl) == (104, t(4), 110, 60)
    assert "range high 104" in row.note


# ---- 5. whole-strategy M2M ----

# Sold CE and PE at 100 (no leg SL). M2M at each close: candle 2: -100+50 = -50; candle 3: -300+50 = -250;
# candle 4: -400+20 = -380 <= -300 -> both legs out at candle 4's closes, at 09:20.
def test_m2m_stop_loss():
    legs = [LegSpec("SELL", "CE"), LegSpec("SELL", "PE")]
    series = {CE: [100, 100, 110, 130, 140, 500], PE: [100, 100, 95, 95, 98, 1]}
    _, tr = run(legs, series, m2m_sl=300)
    assert list(tr.exit_reason) == ["M2M_SL", "M2M_SL"] and list(tr.exit_price) == [140, 98]
    assert list(tr.gross_pnl) == [-400, 20] and set(tr.exit_time) == {t(5)}
    # No look-ahead: changing every candle from 09:20 on changes nothing
    series2 = {CE: series[CE][:5] + [1], PE: series[PE][:5] + [999]}
    _, tr2 = run(legs, series2, m2m_sl=300)
    pd.testing.assert_frame_equal(tr, tr2)


# Target Rs 200: CE falls to 80 -> M2M +200 at candle 3's close.
def test_m2m_target():
    _, tr = run([LegSpec("SELL", "CE")], {CE: [100, 100, 90, 80, 70]}, m2m_target=200)
    assert (tr.iloc[0].exit_reason, tr.iloc[0].gross_pnl, tr.iloc[0].exit_time) == ("M2M_TARGET", 200, t(4))


# Lock at Rs 200 keeping 100, then every Rs 100 more raises the stop by 50.
# M2M closes 150, 250 (lock -> stop 100), 360 (stop 150 from the next minute), 140 <= 150 -> out.
def test_m2m_lock_and_trail():
    _, tr = run([LegSpec("SELL", "CE")], {CE: [100, 100, 85, 75, 64, 86, 50]},
                m2m_lock_at=200, m2m_lock_to=100, m2m_trail_x=100, m2m_trail_y=50)
    row = tr.iloc[0]
    assert (row.exit_reason, row.exit_price, row.gross_pnl, row.exit_time) == ("M2M_TRAIL", 86, 140, t(6))


# M2M stop with trailing (no lock): stop -300, every Rs 100 of profit raises it by 100.
# M2M closes 100 (stop -200), 250 (stop -100 next), then -150 <= -100 -> out.
def test_m2m_trailing_stop():
    _, tr = run([LegSpec("SELL", "CE")], {CE: [100, 100, 90, 75, 115]}, m2m_sl=300, m2m_trail_x=100, m2m_trail_y=100)
    assert (tr.iloc[0].exit_reason, tr.iloc[0].gross_pnl) == ("M2M_TRAIL", -150)


# ---- 6. every exit has a reason, saved with the trade ----

def test_reason_and_note_saved(db_url, db_session):
    from marketlens.backtest.store import load_run
    result = run_options_backtest(settings([LegSpec("SELL", "CE", unit="points", sl=20, target=30)]),
                                  OneDay(day_frame({CE: [100, 100, (100, 121, 69, 100)]})), url=db_url)
    back = load_run(result.extras["run_id"], url=db_url).trades[0]
    assert (back.exit_reason, back.note) == ("SL", BOTH_HIT)


# ---- settings checks ----

@pytest.mark.parametrize("kw", [{"sl": -1}, {"trail_x": 10}, {"trail_x": 10, "trail_y": 5},
                                {"lock_at": 10, "lock_to": 20}, {"reentry": "cost"}, {"unit": "inr"},
                                {"reentry": "again", "max_reentries": 1}])
def test_leg_checks(kw):
    with pytest.raises(ValueError):
        LegSpec("SELL", "CE", **kw)


@pytest.mark.parametrize("kw", [{"m2m_sl": 0}, {"m2m_trail_x": 100, "m2m_trail_y": 50},
                                {"m2m_lock_at": 100, "m2m_lock_to": 200}, {"entry_mode": "signal"},
                                {"entry_mode": "breakout", "range_end": dt.time(9, 15)},
                                {"breakout_side": "middle"}])
def test_settings_checks(kw):
    with pytest.raises(ValueError):
        settings([LegSpec("SELL", "CE")], **kw)
    s = settings([LegSpec("SELL", "CE", sl=30)], entry_mode="breakout", exit_time=dt.time(15, 15))
    assert s.to_params()["range_end"] == "09:30" and "breakout 09:15-09:30" in s.name and "SL 30%" in s.name


# ---- page helpers: templates, input boxes, Strategy builder legs, Strategy Library ----

from marketlens.backtest.options_bt import (TEMPLATES, leg_from_values, legs_from_builder,  # noqa: E402
                                            load_backtest_strategy, save_backtest_strategy, settings_from_params,
                                            template_settings)


# Every template builds and survives a save as JSON and back.
@pytest.mark.parametrize("name", list(TEMPLATES))
def test_templates(name):
    s = template_settings(name, "NIFTY", dt.date(2025, 1, 1), dt.date(2025, 3, 31))
    assert settings_from_params(s.to_params()) == s
    assert template_settings(name, "NIFTY", dt.date(2025, 1, 1), dt.date(2025, 3, 31)).legs[0] is not TEMPLATES[name]["legs"][0]
    with pytest.raises(ValueError):
        template_settings("nope", "NIFTY", dt.date(2025, 1, 1), dt.date(2025, 3, 31))


# 0 in an input box means "off"; no re-entry means max re-entries 0.
def test_leg_from_values():
    leg = leg_from_values({"side": "SELL", "option_type": "PE", "lots": 2, "strike_rule": "DELTA", "value": 0.2,
                           "unit": "points", "sl": 20, "target": 0, "trail_x": 0, "trail_y": 0, "lock_at": 0,
                           "lock_to": 0, "reentry": "none", "max_reentries": 3, "uid": 9})
    assert (leg.sl, leg.target, leg.trail_x, leg.max_reentries, leg.lots) == (20, None, None, 0, 2)


# Builder strikes become ATM +/- N from the spot when it was built (NIFTY step 50, ATM 25,000).
def test_legs_from_builder():
    legs = legs_from_builder({"underlying": "NIFTY", "spot": 25010, "legs": [
        {"side": "SELL", "option_type": "CE", "strike": 25100, "lots": 1},
        {"side": "BUY", "option_type": "PE", "strike": 24800, "lots": 2},
        {"side": "BUY", "option_type": "FUT", "strike": None, "lots": 1}]})
    assert [(x.side, x.option_type, x.value, x.lots) for x in legs] == [("SELL", "CE", 2, 1), ("BUY", "PE", -4, 2)]
    with pytest.raises(ValueError):
        legs_from_builder({"underlying": "NIFTY", "spot": 25010, "legs": []})


# Save to the Strategy Library and read back the same settings; the legs table gets readable rules.
def test_save_backtest_strategy(db_url, db_session):
    from sqlalchemy import select

    from marketlens.models import StrategyLeg
    s = template_settings("iron_condor", "NIFTY", dt.date(2025, 1, 1), dt.date(2025, 3, 31), expiry_which="next")
    sid = save_backtest_strategy("Condor", s, url=db_url)
    assert load_backtest_strategy("Condor", url=db_url) == s
    rows = db_session.scalars(select(StrategyLeg).where(StrategyLeg.strategy_id == sid).order_by(StrategyLeg.leg_no)).all()
    assert [r.strike_rule for r in rows] == ["ATM+4", "ATM+8", "ATM-4", "ATM-8"]
    assert {r.expiry_rule for r in rows} == {"NEXT_WEEKLY"}
    assert save_backtest_strategy("Condor", template_settings("920_straddle", "NIFTY", dt.date(2025, 1, 1),
                                                               dt.date(2025, 3, 31)), url=db_url) == sid
    assert len(load_backtest_strategy("Condor", url=db_url).legs) == 2
    with pytest.raises(ValueError):
        load_backtest_strategy("Missing", url=db_url)
