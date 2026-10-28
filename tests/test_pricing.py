"""Tests for marketlens/options/pricing.py: Black-Scholes, implied volatility and Greeks."""
import math

import pytest

from marketlens.options.pricing import RISK_FREE_RATE, bs_price, greeks, implied_vol, price_bounds

R = RISK_FREE_RATE


# r = 6.5% is a named constant.
def test_risk_free_rate():
    assert RISK_FREE_RATE == 0.065


# ---- textbook values ----

# Classic example: S=100, K=100, 1 year, 5% rate, 20% vol -> call 10.4506, put 5.5735.
def test_textbook_at_the_money():
    assert bs_price(100, 100, 1.0, 0.05, 0.20, "CE") == pytest.approx(10.4506, abs=1e-4)
    assert bs_price(100, 100, 1.0, 0.05, 0.20, "PE") == pytest.approx(5.5735, abs=1e-4)


# Hull's book example: S=42, K=40, 6 months, 10% rate, 20% vol -> call 4.76, put 0.81.
def test_textbook_hull():
    assert bs_price(42, 40, 0.5, 0.10, 0.20, "CE") == pytest.approx(4.76, abs=0.005)
    assert bs_price(42, 40, 0.5, 0.10, 0.20, "PE") == pytest.approx(0.81, abs=0.005)


# ---- put-call parity: call - put = S - K x e^(-rT) ----

@pytest.mark.parametrize("K", [23000, 24500, 25000, 25500, 27000])
@pytest.mark.parametrize("T", [1 / 365, 7 / 365, 0.25])
def test_put_call_parity(K, T):
    S, sigma = 25000, 0.14
    diff = bs_price(S, K, T, R, sigma, "CE") - bs_price(S, K, T, R, sigma, "PE")
    assert diff == pytest.approx(S - K * math.exp(-R * T), abs=1e-6)


# At expiry an option is worth only what it is in the money.
def test_at_expiry():
    assert bs_price(25100, 25000, 0, R, 0.14, "CE") == 100
    assert bs_price(25100, 25000, 0, R, 0.14, "PE") == 0


# ---- implied volatility ----

# Price an option at a known IV, then recover that IV from the price (many strikes, times, IVs).
@pytest.mark.parametrize("option_type", ["CE", "PE"])
@pytest.mark.parametrize("K", [22000, 24000, 25000, 26000, 28000])
@pytest.mark.parametrize("T", [1 / 365, 7 / 365, 30 / 365, 1.0])
@pytest.mark.parametrize("sigma", [0.08, 0.14, 0.35, 1.0])
def test_iv_round_trip(option_type, K, T, sigma):
    S = 25000
    price = bs_price(S, K, T, R, sigma, option_type)
    low, high = price_bounds(S, K, T, R, option_type)
    if not low + 1e-6 < price < high - 1e-6:
        pytest.skip("price too close to its limit to carry IV information")
    iv = implied_vol(price, S, K, T, R, option_type)
    assert bs_price(S, K, T, R, iv, option_type) == pytest.approx(price, abs=1e-5)
    if greeks(S, K, T, R, sigma, option_type)["vega"] > 1e-3:   # IV is only well defined with some vega
        assert iv == pytest.approx(sigma, abs=1e-4)


# Impossible prices give NaN instead of a wrong number or a crash.
@pytest.mark.parametrize("price, K, option_type, T", [
    (50, 24800, "CE", 7 / 365),      # below intrinsic value (200)
    (25001, 25000, "CE", 7 / 365),   # more than the spot itself
    (-1, 25000, "PE", 7 / 365),      # negative
    (100, 25000, "CE", 0),           # already expired
])
def test_iv_impossible(price, K, option_type, T):
    assert math.isnan(implied_vol(price, 25000, K, T, R, option_type))


# A real-looking NIFTY quote: ATM call at Rs 190 with a week left -> IV about 15%.
def test_iv_nifty_example():
    iv = implied_vol(190, 25000, 25000, 7 / 365, R, "CE")
    assert 0.12 < iv < 0.18


# ---- Greeks ----

# Each Greek matches a small "nudge the input and see what happens" calculation.
@pytest.mark.parametrize("option_type", ["CE", "PE"])
def test_greeks_match_finite_differences(option_type):
    S, K, T, sigma = 25000, 25200, 10 / 365, 0.15
    g = greeks(S, K, T, R, sigma, option_type)
    price = lambda **kw: bs_price(kw.get("S", S), K, kw.get("T", T), R, kw.get("sigma", sigma), option_type)
    h = 1.0
    assert g["delta"] == pytest.approx((price(S=S + h) - price(S=S - h)) / (2 * h), abs=1e-4)
    assert g["gamma"] == pytest.approx((price(S=S + h) - 2 * price() + price(S=S - h)) / h ** 2, rel=1e-3)
    assert g["vega"] == pytest.approx(price(sigma=sigma + 0.005) - price(sigma=sigma - 0.005), rel=1e-3)
    one_day = 1 / 365
    assert g["theta"] == pytest.approx(price(T=T - one_day / 2) - price(T=T + one_day / 2), rel=2e-2)


# Signs and sizes for an at-the-money option.
def test_greek_signs():
    ce = greeks(25000, 25000, 7 / 365, R, 0.14, "CE")
    pe = greeks(25000, 25000, 7 / 365, R, 0.14, "PE")
    assert 0.45 < ce["delta"] < 0.6 and -0.55 < pe["delta"] < -0.4
    assert ce["delta"] - pe["delta"] == pytest.approx(1, abs=1e-9)
    assert ce["gamma"] == pytest.approx(pe["gamma"]) and ce["gamma"] > 0
    assert ce["theta"] < 0 and pe["theta"] < 0 and ce["vega"] > 0


# Wrong inputs are refused with a clear message.
def test_bad_inputs():
    with pytest.raises(ValueError):
        bs_price(25000, 25000, 0.1, R, 0, "CE")
    with pytest.raises(ValueError, match="CE"):
        bs_price(25000, 25000, 0.1, R, 0.14, "XX")
    with pytest.raises(ValueError):
        implied_vol(100, 25000, 25000, 0.1, R, "CALL")
