"""Tests for marketlens/simulator/replay.py: the replay clock and virtual order fills.

The day is the hand-made one from test_options_bt_rules (Monday 5-Jan-2026, lot size 10, spot 26,010).
Clock minute M = 09:15 + M; at minute M only candles 0 .. M-1 have finished.

    26000 CE candles: 0: 100   1: 100   2: (o100 h110 l95 c105)   3: (o105 h125 l104 c118)   then 118
"""
import datetime as dt

import pandas as pd
import pytest

from marketlens.backtest.engine import order_costs
from marketlens.backtest.options_bt import DayArrays
from marketlens.costs import CostSettings
from marketlens.simulator.replay import (BOTH_HIT, FIRST_MINUTE, LAST_MINUTE, ReplayClock, SimSession, list_sessions,
                                         save_session, session_orders)
from test_options_bt_rules import CE, DAY, EXPIRY, NO_COSTS, PE, day_frame

CANDLES = {CE: [100, 100, (100, 110, 95, 105), (105, 125, 104, 118)], PE: [80, 81, 82, 83, 84]}


# A session on the hand-made day (no costs unless given).
def session(series=None, costs=NO_COSTS, **kw) -> SimSession:
    day = DayArrays.from_frame(day_frame(series or CANDLES), DAY, EXPIRY)
    return SimSession(day, "NIFTY", lot_size=10, costs=costs, **kw)


# ---- the clock ----

def test_clock_steps_and_jumps():
    c = ReplayClock()
    assert (c.minute, c.time()) == (FIRST_MINUTE, dt.time(9, 16))
    assert c.step(5) == 5 and c.time() == dt.time(9, 21)
    assert c.step(-100) == -5 and c.minute == FIRST_MINUTE          # never before 09:16
    assert c.jump(dt.time(15, 0)) == 344 and c.time() == dt.time(15, 0)
    c.play(wall=0)
    assert c.step(1000) == 30 and c.finished and c.time() == dt.time(15, 30) and not c.playing


# Playing at 2x: 1.7 s -> 3 minutes (the 0.2 s left over counts later); pause stops it.
def test_clock_play_speed():
    c = ReplayClock()
    c.play(wall=0.0)
    c.set_speed(2, wall=0.0)
    assert c.due(wall=1.7) == 3 and c.last_tick == 1.5
    assert c.due(wall=2.0) == 1                                      # 0.2 + 0.3 s = 0.5 s x 2 = 1
    c.set_speed(30, wall=2.0)
    assert c.due(wall=2.5) == 15
    c.pause()
    assert c.due(wall=100) == 0
    with pytest.raises(ValueError):
        c.set_speed(3)


# ---- fills: only the last FINISHED candle is used ----

def test_quote_uses_finished_candles_only():
    sim = session()
    assert sim.quote(26000, "CE") == 100                      # 09:16: candle 0 (09:15) closed at 100
    sim.advance(2)                                            # 09:18: candles 0-2 finished
    assert sim.quote(26000, "CE") == 105 and sim.clock.time() == dt.time(9, 18)
    # Changing every candle from 09:18 on must not change anything seen at 09:18
    future = {CE: [100, 100, (100, 110, 95, 105), 999], PE: [80, 81, 82, 999]}
    other = session(future)
    other.advance(2)
    pd.testing.assert_frame_equal(sim.chain(), other.chain())
    assert sim.spot() == other.spot()
    candles = sim.spot_candles(every=1)
    assert len(candles) == 3 and candles["ts"].max().time() == dt.time(9, 17)


# Sold at 09:17 at 100 with SL 20 points (120): candle 2 (high 110) is fine, candle 3 (high 125) hits it.
def test_sl_fill_by_hand():
    sim = session()
    sim.advance(1)                                            # 09:17
    pos = sim.place("SELL", 26000, "CE", lots=1, sl_points=20)
    assert (pos.entry_price, pos.sl, pos.qty) == (100, 120, 10)
    sim.advance(1)                                            # candle 2 finished: close 105
    assert pos.is_open and sim.pnl() == -50                   # (100 - 105) x 10
    sim.advance(1)                                            # candle 3: high 125 >= 120
    assert (pos.exit_price, pos.reason, pos.exit_minute, pos.gross()) == (120, "SL", 3, -200)
    # one point per minute: 09:16 and 09:17 (no trade yet), 09:18 (-50), 09:19 (-200)
    assert sim.pnl() == -200 and [v for _, v in sim.pnl_curve] == [0, 0, -50, -200]


# Bought PE 26000 at 81 with target 2 points (83): candle 3 closes at 83 (high 83) -> out at 83.
def test_target_and_both_hit_and_gap():
    sim = session()
    sim.advance(1)
    pe = sim.place("BUY", 26000, "PE", target_points=2)
    sim.advance(3)
    assert (pe.reason, pe.exit_price, pe.gross()) == ("TARGET", 83, 20)
    both = session({CE: [100, 100, (100, 121, 69, 100)]})
    both.advance(1)
    pos = both.place("SELL", 26000, "CE", sl_points=20, target_points=30)
    both.advance(1)
    assert (pos.reason, pos.exit_price, pos.note) == ("SL", 120, BOTH_HIT)
    gap = session({CE: [100, 100, (130, 135, 128, 131)]})
    gap.advance(1)
    pos = gap.place("SELL", 26000, "CE", sl_points=20)
    gap.advance(1)
    assert pos.exit_price == 130 and "opened past the SL" in pos.note


# Slippage moves the fill against you; brokerage and taxes come from costs.py.
def test_costs_and_slippage():
    costs = CostSettings(slippage_type="points", slippage_value=1)
    sim = session(costs=costs)
    sim.advance(1)
    pos = sim.place("SELL", 26000, "CE")
    assert pos.entry_price == 99                              # sold 1 point below 100
    sim.advance(1)
    sim.exit(pos.id)
    assert pos.exit_price == 106 and pos.reason == "MANUAL"   # bought back 1 point above 105
    total = order_costs("options", "SELL", 99, 106, 10, CostSettings())
    assert pos.entry_costs + pos.exit_costs == pytest.approx(total)
    assert sim.pnl() == pytest.approx(-70 - total)
    assert sim.summary()["costs"] == pytest.approx(total)


# Exit all, the day end, and no trading after 15:30.
def test_exit_all_and_day_end():
    sim = session()
    sim.advance(1)
    a, b = sim.place("SELL", 26000, "CE"), sim.place("BUY", 26000, "PE", lots=2)
    sim.advance(1)
    assert sim.exit_all() == 2 and {a.reason, b.reason} == {"EXIT_ALL"}
    c = sim.place("SELL", 26000, "CE")
    sim.advance(LAST_MINUTE)
    assert sim.clock.finished and c.reason == "DAY_END" and c.exit_price == 118
    with pytest.raises(ValueError, match="over"):
        sim.place("BUY", 26000, "CE")
    summary = sim.summary()
    assert summary["trades"] == 3 and summary["closed"] == 3


# Jump forward replays every minute (the SL still works); going back is refused once you have trades.
def test_jump():
    sim = session()
    sim.jump(dt.time(9, 17))
    pos = sim.place("SELL", 26000, "CE", sl_points=20)
    sim.jump(dt.time(10, 0))
    assert pos.reason == "SL" and pos.exit_minute == 3
    with pytest.raises(ValueError, match="new session"):
        sim.jump(dt.time(9, 20))
    fresh = session()
    fresh.jump(dt.time(10, 0))
    assert fresh.jump(dt.time(9, 20)) < 0 and fresh.clock.time() == dt.time(9, 20)


# Bad orders are refused.
def test_order_checks():
    sim = session()
    with pytest.raises(ValueError):
        sim.place("HOLD", 26000, "CE")
    with pytest.raises(ValueError):
        sim.place("BUY", 26000, "CE", lots=0)
    with pytest.raises(ValueError):
        sim.place("BUY", 99999, "CE")
    with pytest.raises(ValueError):
        sim.place("BUY", 26000, "CE", sl_points=-5)
    with pytest.raises(ValueError):
        sim.exit(42)


# The chain shows LTP, IV, delta and OI; open positions become payoff legs.
def test_chain_and_payoff_legs():
    sim = session()
    sim.advance(1)
    chain = sim.chain().set_index("strike")
    assert chain.loc[26000, "ce_ltp"] == 100 and chain.loc[26000, "pe_ltp"] == 81
    assert 0.4 < chain.loc[26000, "ce_delta"] < 0.6 and chain.loc[26000, "ce_oi"] == 1
    sim.place("SELL", 26000, "CE")
    legs = sim.open_legs()
    assert len(legs) == 1 and legs[0].strike == 26000 and legs[0].iv == 14.0


# ---- saving (test database only) ----

def test_save_and_list(db_url, db_session):
    sim = session()
    sim.advance(1)
    sim.place("SELL", 26000, "CE", sl_points=20)
    sim.advance(3)
    sid = save_session(sim, notes="practice", url=db_url)
    runs = list_sessions(url=db_url)
    assert runs.iloc[0]["id"] == sid and runs.iloc[0]["orders"] == 2 and runs.iloc[0]["pnl"] == -200
    orders = session_orders(sid, url=db_url)
    assert list(orders["reason"]) == ["OPEN", "SL"] and list(orders["side"]) == ["SELL", "BUY"]
    assert orders["pnl"].iloc[1] == -200
    sim.place("BUY", 26000, "PE")
    assert save_session(sim, url=db_url) == sid                       # saving again updates the same session
    assert session_orders(sid, url=db_url)["reason"].tolist() == ["OPEN", "SL", "OPEN"]
