"""Tests for marketlens/options/chain.py (payoff tests are in test_strategies.py)."""
import datetime as dt
import math

import pandas as pd
import pytest

from marketlens.broker.candles import IST
from marketlens.broker.demo_data import DemoDataClient
from marketlens.options.chain import (atm_straddle, atm_strike, chain_summary, chain_view, expected_move,
                                      make_leg, max_pain, pain_curve, pcr)


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


# A builder leg takes its premium and IV from the chain.
def test_make_leg(small):
    leg = make_leg(small, "nifty", dt.date(2026, 11, 3), 25000, "pe", "sell", lots=2, lot_size=65)
    assert (leg.side, leg.option_type, leg.strike, leg.entry_price, leg.lots, leg.lot_size, leg.iv) ==         ("SELL", "PE", 25000.0, 110.0, 2, 65, 14.5)
    assert leg.expiry == dt.date(2026, 11, 3) and leg.qty == 130 and leg.sign == -1
    with pytest.raises(ValueError):
        make_leg(small, "NIFTY", dt.date(2026, 11, 3), 99999, "CE")
