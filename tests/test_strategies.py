"""Tests for marketlens/options/strategies.py: legs, payoff, max profit/loss, breakevens,
net Greeks, probability of profit, templates, approximate margin and the Strategy Library."""
import datetime as dt
import math

import numpy as np
import pytest

from marketlens.broker.candles import IST
from marketlens.broker.demo_data import DemoDataClient
from marketlens.options.pricing import RISK_FREE_RATE, bs_price, greeks, norm_cdf
from marketlens.options.strategies import (TEMPLATES, Leg, analyse, approximate_margin, breakevens, build_template,
                                           load_strategy, max_loss, max_profit, net_greeks, net_premium,
                                           margin_for, payoff, price_range, probability_of_profit, save_strategy,
                                           years_left)
from marketlens.options.strike_select import chain_to_long

EXP = dt.date(2026, 11, 3)
NOW = dt.datetime(2026, 10, 28, 11, 0, tzinfo=IST)
R = RISK_FREE_RATE


# Short-hand for making legs in the tests.
def leg(side, kind, strike, price, lots=1, lot_size=1, expiry=EXP, iv=14.0):
    return Leg(side, kind, strike, expiry, lots, price, lot_size, iv)


# ---- the Leg itself ----

def test_leg_checks():
    l = Leg("buy", "ce", 25000, "2026-11-03", 2, 120, 65)
    assert (l.side, l.option_type, l.expiry, l.qty, l.sign) == ("BUY", "CE", EXP, 130, 1)
    assert Leg("SELL", "FUT", None, EXP, 1, 25100, 65).strike is None
    for bad in [("HOLD", "CE", 25000), ("BUY", "XX", 25000), ("BUY", "CE", None)]:
        with pytest.raises(ValueError):
            Leg(*bad, EXP)


# ---- payoff at expiry ----

# Bought call: -premium below the strike, intrinsic - premium above.
def test_long_call_payoff():
    pnl = payoff([leg("BUY", "CE", 25000, 100, lot_size=65)], [24000, 25000, 25100, 25500])
    assert list(pnl) == [-6500, -6500, 0, 26000]


# Futures: (price - entry) x qty, sign by side.
def test_future_payoff():
    pnl = payoff([Leg("SELL", "FUT", None, EXP, 1, 25100, 65)], [25000, 25100, 25300])
    assert list(pnl) == [6500, 0, -13000]


# ---- payoff on a date (Black-Scholes) ----

# Before expiry each option is valued with Black-Scholes at its own time left.
def test_payoff_today_is_black_scholes():
    l = leg("BUY", "CE", 25000, 100, lot_size=65, iv=15)
    T = years_left(NOW, EXP)
    expected = (bs_price(25100, 25000, T, R, 0.15, "CE") - 100) * 65
    assert payoff([l], [25100], at=NOW)[0] == pytest.approx(expected)
    assert payoff([l], [25100], at=EXP)[0] == pytest.approx((100 - 100) * 65)    # a date = 15:30 that day


# Calendar spread at the near expiry: the near leg is intrinsic, the far leg still has time value.
def test_calendar_at_near_expiry():
    legs = [leg("SELL", "CE", 25000, 120), leg("BUY", "CE", 25000, 200, expiry=dt.date(2026, 11, 10))]
    pnl = payoff(legs, [25000])[0]
    far_value = bs_price(25000, 25000, 7 / 365, R, 0.14, "CE")
    assert pnl == pytest.approx(120 + far_value - 200)
    assert pnl > 0       # a calendar earns most when the price ends at the strike


# ---- breakevens, max profit, max loss ----

def test_short_straddle_numbers():
    legs = [leg("SELL", "CE", 25000, 150, lot_size=65), leg("SELL", "PE", 25000, 140, lot_size=65)]
    prices = price_range(25000, legs=legs)
    pnl = payoff(legs, prices)
    assert breakevens(prices, pnl) == [pytest.approx(24710, abs=0.01), pytest.approx(25290, abs=0.01)]
    assert max_profit(legs, prices) == pytest.approx(290 * 65) and max_loss(legs, prices) is None
    assert net_premium(legs) == 290 * 65


def test_spreads_are_limited():
    bull = [leg("BUY", "CE", 25000, 100, lot_size=65), leg("SELL", "CE", 25200, 30, lot_size=65)]
    prices = price_range(25000, legs=bull)
    assert max_profit(bull, prices) == pytest.approx((200 - 70) * 65)
    assert max_loss(bull, prices) == pytest.approx(-70 * 65)
    assert breakevens(prices, payoff(bull, prices)) == [pytest.approx(25070, abs=0.01)]


# A sold put's worst case is at a price of 0, far outside the drawn range.
def test_sold_put_worst_case():
    put = [leg("SELL", "PE", 25000, 100)]
    assert max_loss(put, price_range(25000, 5)) == pytest.approx(-(25000 - 100))


# Exact even when the spot is not on a strike (corners are always checked).
def test_max_profit_off_grid():
    legs = [leg("SELL", "CE", 22750, 190.2, lot_size=65), leg("SELL", "PE", 22750, 180.2, lot_size=65)]
    assert max_profit(legs, price_range(22749.75, legs=legs)) == pytest.approx(370.4 * 65)


# ---- net Greeks ----

def test_net_greeks_are_sums():
    legs = [leg("BUY", "CE", 25000, 100, lot_size=65, iv=14), leg("SELL", "PE", 24800, 60, lot_size=65, iv=15)]
    T = years_left(NOW, EXP)
    g1, g2 = greeks(25010, 25000, T, R, 0.14, "CE"), greeks(25010, 24800, T, R, 0.15, "PE")
    net = net_greeks(legs, 25010, NOW)
    for key in ("delta", "gamma", "theta", "vega"):
        assert net[key] == pytest.approx(65 * g1[key] - 65 * g2[key])
    assert net_greeks([Leg("BUY", "FUT", None, EXP, 2, 25000, 65)], 25010, NOW)["delta"] == 130


# A short straddle is nearly delta-neutral, earns time decay and loses if IV rises.
def test_short_straddle_greeks():
    legs = [leg("SELL", "CE", 25000, 150, lot_size=65), leg("SELL", "PE", 25000, 140, lot_size=65)]
    g = net_greeks(legs, 25000, NOW)
    assert abs(g["delta"]) < 10 and g["theta"] > 0 and g["vega"] < 0 and g["gamma"] < 0


# ---- probability of profit ----

# For one bought call it must equal the closed formula: P(S_T > strike + premium).
def test_pop_matches_formula():
    S, K, p, iv = 25000, 25000, 150, 14
    T = years_left(NOW, EXP)
    d2 = (math.log(S / (K + p)) + (R - (iv / 100) ** 2 / 2) * T) / (iv / 100 * math.sqrt(T))
    pop = probability_of_profit([leg("BUY", "CE", K, p, iv=iv)], S, iv, NOW)
    assert pop == pytest.approx(norm_cdf(d2), abs=2e-3)


# At fair (Black-Scholes) premiums a short straddle wins more often than the long one,
# and the two chances add up to 1. (Sold too cheaply it would win less than half the time.)
def test_pop_buyer_vs_seller():
    T = years_left(NOW, EXP)
    ce, pe = bs_price(25000, 25000, T, R, 0.14, "CE"), bs_price(25000, 25000, T, R, 0.14, "PE")
    buy = [leg("BUY", "CE", 25000, ce), leg("BUY", "PE", 25000, pe)]
    sell = [leg("SELL", "CE", 25000, ce), leg("SELL", "PE", 25000, pe)]
    pb, ps = (probability_of_profit(x, 25000, 14, NOW) for x in (buy, sell))
    assert ps > 0.5 > pb and pb + ps == pytest.approx(1, abs=1e-3)


# ---- templates ----

# A DEMO chain in long shape, and a far-expiry chain for the calendar.
@pytest.fixture
def chains():
    demo = DemoDataClient(spots={"NIFTY": 25010}, vix=14, now=NOW)
    near = demo.get_option_chain("NIFTY", EXP)
    far = demo.get_option_chain("NIFTY", dt.date(2026, 11, 10))
    return chain_to_long(near.table), chain_to_long(far.table)


@pytest.mark.parametrize("name, count, net_credit", [
    ("long_straddle", 2, False), ("short_straddle", 2, True), ("long_strangle", 2, False),
    ("short_strangle", 2, True), ("iron_condor", 4, True), ("iron_butterfly", 4, True),
    ("bull_call_spread", 2, False), ("bear_put_spread", 2, False), ("bull_put_spread", 2, True),
    ("bear_call_spread", 2, True), ("ratio_spread", 2, True),
])
def test_templates(chains, name, count, net_credit):
    legs = build_template(name, chains[0], 25010, EXP, lot_size=65, gap=2, wing=2)
    assert len(legs) == count and all(l.entry_price > 0 and l.iv for l in legs)
    assert (net_premium(legs) > 0) == net_credit


# Iron condor strikes: sold CE at ATM+2, bought CE at ATM+4, sold PE at ATM-2, bought PE at ATM-4; risk is limited.
def test_iron_condor_strikes(chains):
    legs = build_template("iron_condor", chains[0], 25010, EXP, 65, gap=2, wing=2)
    assert [(l.side, l.option_type, l.strike) for l in legs] == [
        ("SELL", "CE", 25100), ("BUY", "CE", 25200), ("SELL", "PE", 24900), ("BUY", "PE", 24800)]
    prices = price_range(25010, legs=legs)
    assert max_loss(legs, prices) is not None and max_profit(legs, prices) == pytest.approx(net_premium(legs))


# Calendar: sell near, buy far, same ATM strike; custom starts empty; unknown names refused.
def test_calendar_custom_unknown(chains):
    legs = build_template("calendar_spread", chains[0], 25010, EXP, 65, far_options=chains[1],
                          far_expiry=dt.date(2026, 11, 10))
    assert [(l.side, l.expiry) for l in legs] == [("SELL", EXP), ("BUY", dt.date(2026, 11, 10))]
    assert legs[0].strike == legs[1].strike == 25000 and net_premium(legs) < 0
    assert build_template("custom", chains[0], 25010, EXP) == [] and len(TEMPLATES) == 13
    with pytest.raises(ValueError):
        build_template("calendar_spread", chains[0], 25010, EXP)
    with pytest.raises(ValueError):
        build_template("butterfly_of_doom", chains[0], 25010, EXP)


# ---- everything together, and margin ----

def test_analyse(chains):
    legs = build_template("iron_condor", chains[0], 25010, EXP, 65)
    a = analyse(legs, 25010, 14, now=NOW)
    assert a["risk_reward"] == pytest.approx(a["max_profit"] / abs(a["max_loss"]))
    assert len(a["breakevens"]) == 2 and 0 < a["pop"] < 1
    assert len(a["today_pnl"]) == len(a["prices"]) and a["greeks"]["theta"] > 0
    # Selling strikes further away (wider condor) makes winning more likely
    wide = analyse(build_template("iron_condor", chains[0], 25010, EXP, 65, gap=8), 25010, 14, now=NOW)
    assert wide["pop"] > a["pop"]


# Covered spreads: max loss + 2% exposure; naked selling ~12% of the larger uncovered side;
# bought options only: the premium.
def test_approximate_margin():
    spread = [leg("SELL", "PE", 25000, 100, lot_size=65), leg("BUY", "PE", 24800, 40, lot_size=65)]
    assert approximate_margin(spread, 25000) == pytest.approx((200 - 60) * 65 + 0.02 * 25000 * 65)
    straddle = [leg("SELL", "CE", 25000, 150, lot_size=65), leg("SELL", "PE", 25000, 140, lot_size=65)]
    assert approximate_margin(straddle, 25000) == pytest.approx(0.12 * 25000 * 65)
    assert approximate_margin([leg("BUY", "CE", 25000, 100, lot_size=65)], 25000) == pytest.approx(6500)


# ---- Strategy Library (TEST database) ----

def test_save_and_load(db_url, db_session, chains):
    legs = build_template("iron_condor", chains[0], 25010, EXP, 65)
    strikes = chains[0]["strike"].unique()
    sid = save_strategy("My condor", legs, "NIFTY", 25010, strikes, "iron_condor", url=db_url)
    info, back = load_strategy("My condor", url=db_url)
    assert info["id"] == sid and info["underlying"] == "NIFTY" and back == legs
    assert info["rules"] == ["SELL CE ATM+2 NEAREST", "BUY CE ATM+4 NEAREST",
                             "SELL PE ATM-2 NEAREST", "BUY PE ATM-4 NEAREST"]
    # Saving again with the same name replaces the legs instead of adding a second strategy
    assert save_strategy("My condor", legs[:2], "NIFTY", 25010, strikes, url=db_url) == sid
    assert len(load_strategy("My condor", url=db_url)[1]) == 2
    with pytest.raises(ValueError):
        save_strategy("Empty", [], "NIFTY", 25010, strikes, url=db_url)


# Without Dhan (DEMO client or no client) the margin is the labelled estimate.
def test_margin_for_without_dhan():
    legs = [leg("SELL", "CE", 25000, 150, lot_size=65)]
    m = margin_for(legs, "NIFTY", 25000, DemoDataClient(spots={"NIFTY": 25000}, vix=14, now=NOW))
    assert m["source"] == "approximate" and "check with your broker" in m["note"]
    assert m["amount"] == approximate_margin(legs, 25000)


# With a (fake) Dhan client, each leg is looked up and Dhan's number is used.
def test_margin_for_with_dhan(monkeypatch):
    from marketlens.broker import dhan_instruments
    from marketlens.broker.dhan_instruments import Security
    monkeypatch.setattr(dhan_instruments, "find_option", lambda u, e, k, t: Security(
        f"{u}-{k}-{t}", "40037", "NSE_FNO", "OPTIDX", "NSE", "option", expiry=e, strike=k, option_type=t))

    class FakeDhan:
        is_demo = False
        asked = None

        def is_ready(self):
            return True

        def get_margin(self, orders):
            FakeDhan.asked = orders
            return {"total_margin": 123456.0}
    m = margin_for([leg("SELL", "CE", 25000, 150, lot_size=65)], "NIFTY", 25000, FakeDhan())
    assert m == {"amount": 123456.0, "source": "Dhan margin calculator", "note": ""}
    assert FakeDhan.asked[0]["qty"] == 65 and FakeDhan.asked[0]["side"] == "SELL"
