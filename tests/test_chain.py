"""Tests for marketlens/options/chain.py and marketlens/options/payoff.py."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from marketlens.broker.candles import IST
from marketlens.broker.demo_data import DemoDataClient
from marketlens.options.chain import (atm_straddle, atm_strike, chain_summary, chain_view, expected_move,
                                      make_leg, max_pain, pain_curve, pcr)
from marketlens.options.payoff import (breakevens, leg_payoff, payoff_summary, price_range,
                                       strategy_payoff)


# A tiny hand-made chain with 3 strikes.
@pytest.fixture
def small():
    return pd.DataFrame({"strike": [24900.0, 25000.0, 25100.0],
                         "ce_ltp": [180.0, 120.0, 75.0], "pe_ltp": [70.0, 110.0, 165.0],
                         "ce_oi": [100.0, 200.0, 500.0], "pe_oi": [400.0, 300.0, 100.0],
                         "ce_iv": [14.0, 13.5, 13.2], "pe_iv": [14.5, 14.5, 14.0]})


# ---- chain numbers ----

def test_atm_and_pcr(small):
    assert atm_strike(small, 25020) == 25000 and atm_strike(small, 25060) == 25100
    assert pcr(small) == round(800 / 800, 2)
    assert pcr(small.assign(ce_oi=0)) is None


# Max pain worked by hand (calls pay OI x (price - strike), puts pay OI x (strike - price)):
#   at 24,900: puts 25000 (300 x 100) + 25100 (100 x 200)            = 50,000
#   at 25,000: calls 24900 (100 x 100) + puts 25100 (100 x 100)      = 20,000  <- smallest
#   at 25,100: calls 24900 (100 x 200) + 25000 (200 x 100)           = 40,000
def test_max_pain_by_hand(small):
    curve = pain_curve(small).set_index("price")["total"]
    assert curve[24900] == 300 * 100 + 100 * 200
    assert curve[25000] == 100 * 100 + 100 * 100
    assert curve[25100] == 100 * 200 + 200 * 100
    assert max_pain(small) == 25000
    assert max_pain(small.assign(ce_oi=0, pe_oi=0)) is None


# ATM straddle = ATM call + ATM put; expected move = spot x IV x sqrt(days/365).
def test_straddle_and_expected_move(small):
    assert atm_straddle(small, 25010) == 230.0
    assert expected_move(25000, 14.0, 7) == pytest.approx(25000 * 0.14 * math.sqrt(7 / 365))
    assert expected_move(25000, None, 7, straddle=230) == pytest.approx(184.0)    # 0.8 x straddle
    s = chain_summary(small, 25010, dt.date(2026, 11, 3), now=dt.datetime(2026, 10, 27, 15, 30, tzinfo=IST))
    assert s["atm"] == 25000 and s["days"] == pytest.approx(7) and s["atm_iv"] == pytest.approx(14.0)
    assert s["expected_move_pct"] == pytest.approx(14.0 * math.sqrt(7 / 365))


# The view keeps `width` strikes each side of ATM and marks in-the-money strikes.
def test_chain_view():
    chain = DemoDataClient(spots={"NIFTY": 25010}, vix=14,
                           now=dt.datetime(2026, 10, 28, 11, tzinfo=IST)).get_option_chain("NIFTY", dt.date(2026, 11, 3))
    view = chain_view(chain.table, chain.spot, width=3)
    assert list(view["strike"]) == [24850, 24900, 24950, 25000, 25050, 25100, 25150]
    assert view["is_atm"].sum() == 1 and view.loc[view["is_atm"], "strike"].item() == 25000
    assert list(view["ce_itm"]) == [True] * 4 + [False] * 3      # calls below spot are in the money
    assert list(view["pe_itm"]) == [False] * 4 + [True] * 3


# A builder leg takes its premium from the chain.
def test_make_leg(small):
    leg = make_leg(small, "nifty", dt.date(2026, 11, 3), 25000, "pe", "sell", lots=2, lot_size=65)
    assert leg == {"side": "SELL", "option_type": "PE", "strike": 25000.0, "premium": 110.0, "lots": 2,
                   "lot_size": 65, "expiry": dt.date(2026, 11, 3), "underlying": "NIFTY"}
    with pytest.raises(ValueError):
        make_leg(small, "NIFTY", dt.date(2026, 11, 3), 99999, "CE")


# ---- payoff ----

CALL = {"side": "BUY", "option_type": "CE", "strike": 25000, "premium": 100, "lots": 1, "lot_size": 65}


# A bought call: loses the premium below the strike, gains above it.
def test_long_call():
    pnl = leg_payoff(CALL, np.array([24000.0, 25000, 25100, 25500]))
    assert list(pnl) == [-6500, -6500, 0, 26000]


# Short straddle: max profit = premiums received, breakevens = strike +/- total premium, unlimited loss.
def test_short_straddle():
    legs = [{"side": "SELL", "option_type": "CE", "strike": 25000, "premium": 150, "lots": 1, "lot_size": 65},
            {"side": "SELL", "option_type": "PE", "strike": 25000, "premium": 140, "lots": 1, "lot_size": 65}]
    prices = price_range(25000)
    s = payoff_summary(legs, prices)
    assert s["max_profit"] == pytest.approx(290 * 65) and s["max_loss"] is None
    assert s["breakevens"] == [pytest.approx(24710, abs=0.01), pytest.approx(25290, abs=0.01)]
    assert s["net_premium"] == 290 * 65


# Bull call spread: limited both ways; profit capped at the strike gap minus net premium.
def test_bull_call_spread():
    legs = [CALL, {"side": "SELL", "option_type": "CE", "strike": 25200, "premium": 30, "lots": 1, "lot_size": 65}]
    s = payoff_summary(legs, price_range(25000))
    assert s["max_profit"] == pytest.approx((200 - 70) * 65) and s["max_loss"] == pytest.approx(-70 * 65)
    assert s["breakevens"] == [pytest.approx(25070, abs=0.01)]


# A sold put's worst case is at a price of 0, even outside the drawn range.
def test_sold_put_worst_case():
    put = {"side": "SELL", "option_type": "PE", "strike": 25000, "premium": 100, "lots": 1, "lot_size": 1}
    s = payoff_summary([put], price_range(25000, pct=5))
    assert s["max_loss"] == pytest.approx(-(25000 - 100)) and s["max_profit"] == pytest.approx(100)


# Breakevens are found between the drawn points by a straight line.
def test_breakevens_interpolated():
    assert breakevens(np.array([0.0, 10.0]), np.array([-5.0, 5.0])) == [5.0]
    assert strategy_payoff([], np.array([1.0, 2.0])).tolist() == [0.0, 0.0]


# Max profit is exact even when no chart point lands on the strike (spot 22,749.75, strike 22,750).
def test_max_profit_exact_off_grid():
    legs = [{"side": "SELL", "option_type": "CE", "strike": 22750, "premium": 190.2, "lots": 1, "lot_size": 65},
            {"side": "SELL", "option_type": "PE", "strike": 22750, "premium": 180.2, "lots": 1, "lot_size": 65}]
    s = payoff_summary(legs, price_range(22749.75))
    assert s["max_profit"] == pytest.approx(370.4 * 65) == s["net_premium"]
