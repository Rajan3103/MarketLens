"""Tests for marketlens/options/strike_select.py (shared by the builder and the backtester)."""
import datetime as dt

import pandas as pd
import pytest

from marketlens.broker.candles import IST
from marketlens.broker.demo_data import DemoDataClient
from marketlens.options.strike_select import (atm, atm_offset_strike, chain_to_long, fill_delta, offset_of,
                                              strike_for_delta, strike_for_premium, strike_step)

STRIKES = [24800.0, 24850, 24900, 24950, 25000, 25050, 25100, 25150, 25200]


# A DEMO chain in the long shape (strike, option_type, price, iv, delta, oi).
@pytest.fixture
def options():
    chain = DemoDataClient(spots={"NIFTY": 25010}, vix=14, now=dt.datetime(2026, 10, 28, 11, tzinfo=IST)) \
        .get_option_chain("NIFTY", dt.date(2026, 11, 3))
    return chain_to_long(chain.table)


# ATM, the strike gap, and ATM +/- N strikes.
def test_atm_offsets():
    assert strike_step(STRIKES) == 50 and atm(STRIKES, 25020) == 25000 and atm(STRIKES, 25025) == 25000
    assert atm_offset_strike(STRIKES, 25010, 0) == 25000
    assert atm_offset_strike(STRIKES, 25010, 2) == 25100 and atm_offset_strike(STRIKES, 25010, -3) == 24850
    assert offset_of(25100, STRIKES, 25010) == 2 and offset_of(24850, STRIKES, 25010) == -3
    with pytest.raises(ValueError, match="outside"):
        atm_offset_strike(STRIKES, 25010, 9)


# The long shape has one row per strike and type.
def test_chain_to_long(options):
    assert set(options["option_type"]) == {"CE", "PE"} and len(options) == 2 * 41
    assert list(options.columns) == ["strike", "option_type", "price", "iv", "delta", "oi"]


# Closest to Rs 50: a call above the spot, a put below it, with the price really close to 50.
@pytest.mark.parametrize("kind", ["CE", "PE"])
def test_strike_for_premium(options, kind):
    k = strike_for_premium(options, kind, 50)
    rows = options[options["option_type"] == kind]
    price = rows.loc[rows["strike"] == k, "price"].item()
    assert abs(price - 50) == (rows["price"] - 50).abs().min()
    assert (k > 25010) if kind == "CE" else (k < 25010)


# Hand-made table: closest premium and a tie that goes to the cheaper (further) strike.
def test_strike_for_premium_by_hand():
    table = pd.DataFrame({"strike": [100.0, 110, 120], "option_type": "CE", "price": [60.0, 45, 55],
                          "iv": None, "delta": None})
    assert strike_for_premium(table, "CE", 50) == 120          # 45 and 55 tie -> further strike
    assert strike_for_premium(table, "CE", 58) == 100


# Closest to delta 0.20: about 0.20 for the call and about -0.20 for the put.
@pytest.mark.parametrize("kind", ["CE", "PE"])
def test_strike_for_delta(options, kind):
    k = strike_for_delta(options, kind, 0.20)
    delta = options[(options["option_type"] == kind) & (options["strike"] == k)]["delta"].item()
    assert abs(abs(delta) - 0.20) < 0.06 and (delta > 0) == (kind == "CE")


# Stored minute data has no delta: it is worked out from IV, giving the same strike.
def test_delta_from_iv(options):
    no_delta = options.assign(delta=None)
    years = 6.19 / 365      # 28-Oct 11:00 to 3-Nov 15:30
    assert fill_delta(no_delta, 25010, years)["delta"].notna().all()
    assert strike_for_delta(no_delta, "PE", 0.20, spot=25010, years=years) == strike_for_delta(options, "PE", 0.20)
    with pytest.raises(ValueError, match="deltas"):
        strike_for_delta(no_delta, "PE", 0.20)
