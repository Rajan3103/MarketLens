"""Tests for marketlens/options/pricing.py (Black-Scholes)."""
import pytest

from marketlens.options.pricing import bs_greeks, bs_price


# The textbook example: S=100, K=100, 1 year, 5% rate, 20% vol -> call 10.45, put 5.57.
def test_textbook_values():
    assert bs_price(100, 100, 1.0, 0.20, "CE", rate=0.05) == pytest.approx(10.4506, abs=1e-3)
    assert bs_price(100, 100, 1.0, 0.20, "PE", rate=0.05) == pytest.approx(5.5735, abs=1e-3)


# Put-call parity: call - put = spot - strike x discount.
@pytest.mark.parametrize("strike", [24000, 25000, 26000])
def test_put_call_parity(strike):
    import math
    s, t, v, r = 25000, 7 / 365, 0.14, 0.065
    diff = bs_price(s, strike, t, v, "CE", r) - bs_price(s, strike, t, v, "PE", r)
    assert diff == pytest.approx(s - strike * math.exp(-r * t), abs=0.01)


# At expiry an option is worth only what it is in the money.
def test_at_expiry():
    assert bs_price(25100, 25000, 0, 0.14, "CE") == 100
    assert bs_price(25100, 25000, 0, 0.14, "PE") == 0


# Greeks have the expected signs and sizes for an at-the-money option.
def test_greeks():
    ce = bs_greeks(25000, 25000, 7 / 365, 0.14, "CE")
    pe = bs_greeks(25000, 25000, 7 / 365, 0.14, "PE")
    assert 0.45 < ce["delta"] < 0.6 and -0.55 < pe["delta"] < -0.4
    assert ce["delta"] - pe["delta"] == pytest.approx(1, abs=1e-9)
    assert ce["gamma"] == pytest.approx(pe["gamma"]) and ce["gamma"] > 0
    assert ce["theta"] < 0 and pe["theta"] < 0      # options lose value every day
    assert ce["vega"] > 0


# Wrong inputs are refused.
def test_bad_inputs():
    with pytest.raises(ValueError):
        bs_price(25000, 25000, 0.1, 0, "CE")
    with pytest.raises(ValueError):
        bs_price(25000, 25000, 0.1, 0.14, "XX")
