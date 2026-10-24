"""Tests for marketlens/costs.py.

Each expected number was worked out by hand from the rates at the top of
costs.py, so if a rate changes, these numbers must be updated too.
"""
import pytest

from marketlens.costs import CostSettings, round_trip_costs, to_paise, trade_costs


# Intraday BUY 1000 shares at Rs 100 (turnover Rs 1,00,000).
def test_equity_intraday_buy():
    c = trade_costs("equity_intraday", "BUY", price=100, qty=1000)
    assert c.turnover == 100000
    assert c.brokerage == 20.00   # Rs 20 is lower than 0.03% (Rs 30)
    assert c.stt == 0             # intraday STT only on SELL
    assert c.exchange == 3.07     # 0.00307%
    assert c.sebi == 0.10         # Rs 10 per crore
    assert c.stamp == 3.00        # 0.003% on BUY
    assert c.gst == 4.17          # 18% of (20 + 3.07 + 0.10)
    assert c.charges == 30.34


# A small intraday order pays 0.03% because it is lower than Rs 20.
def test_brokerage_cap_on_small_order():
    c = trade_costs("equity_intraday", "SELL", price=100, qty=10)  # turnover Rs 1,000
    assert c.brokerage == 0.30


# Option SELL: 1 NIFTY lot (75) at premium Rs 200 pays 0.15% STT.
def test_options_sell():
    c = trade_costs("options", "SELL", price=200, qty=75)  # turnover Rs 15,000
    assert c.brokerage == 20.00   # flat per order, no cap for options
    assert c.stt == 22.50         # 0.15% of premium on SELL
    assert c.exchange == 5.33     # 0.03553%
    assert c.sebi == 0.02
    assert c.stamp == 0           # stamp duty only on BUY
    assert c.gst == 4.56
    assert c.charges == 52.41


# Option BUY: no STT, but stamp duty.
def test_options_buy():
    c = trade_costs("options", "BUY", price=200, qty=75)
    assert c.stt == 0
    assert c.stamp == 0.45        # 0.003% of Rs 15,000
    assert c.charges == 30.36


# Delivery: zero brokerage but 0.1% STT on BUY as well as SELL.
def test_equity_delivery_buy():
    c = trade_costs("equity_delivery", "BUY", price=2500, qty=10)  # turnover Rs 25,000
    assert c.brokerage == 0
    assert c.stt == 25.00
    assert c.stamp == 3.75        # 0.015% on BUY
    assert c.charges == 29.69
    sell = trade_costs("equity_delivery", "SELL", price=2500, qty=10)
    assert sell.stt == 25.00 and sell.stamp == 0


# Futures SELL: 1 lot of NIFTY at 25,000 pays 0.05% STT.
def test_futures_sell():
    c = trade_costs("futures", "SELL", price=25000, qty=75)  # turnover Rs 18,75,000
    assert c.brokerage == 20.00
    assert c.stt == 937.50
    assert c.exchange == 34.31
    assert c.sebi == 1.88
    assert c.gst == 10.11
    assert c.charges == 1003.80


# With taxes switched off, only brokerage is left.
def test_without_taxes():
    c = trade_costs("options", "SELL", 200, 75, CostSettings(include_taxes=False))
    assert c.charges == 20.00
    assert c.stt == c.gst == c.stamp == 0


# Slippage in percent and in points.
def test_slippage():
    pct = trade_costs("options", "BUY", 200, 75, CostSettings(slippage_type="pct", slippage_value=0.5))
    assert pct.slippage == 75.00          # 0.5% of 200 = Rs 1 per unit x 75
    pts = trade_costs("options", "BUY", 200, 75, CostSettings(slippage_type="points", slippage_value=0.5))
    assert pts.slippage == 37.50          # Rs 0.50 per unit x 75
    assert pts.total == to_paise(pts.charges + 37.50)


# Changing brokerage on a page changes the result.
def test_custom_brokerage():
    c = trade_costs("options", "SELL", 200, 75, CostSettings(brokerage_per_order=10))
    assert c.brokerage == 10
    no_cap = trade_costs("equity_intraday", "BUY", 100, 10, CostSettings(brokerage_cap_pct=None))
    assert no_cap.brokerage == 20         # always the flat amount


# A round trip adds the opening and closing order.
def test_round_trip():
    total = round_trip_costs("options", 200, 200, 75, entry_side="SELL")
    assert total == to_paise(52.41 + 30.36)


# Wrong inputs are refused with a clear message.
@pytest.mark.parametrize("args", [
    ("crypto", "BUY", 100, 1), ("options", "HOLD", 100, 1),
    ("options", "BUY", 0, 1), ("options", "BUY", 100, -5),
])
def test_bad_inputs(args):
    with pytest.raises(ValueError):
        trade_costs(*args)


# Settings with negative numbers or an unknown slippage type are refused.
def test_bad_settings():
    with pytest.raises(ValueError):
        CostSettings(slippage_type="ticks")
    with pytest.raises(ValueError):
        CostSettings(brokerage_per_order=-1)


# Rupee rounding goes up at half a paisa, unlike Python's round().
def test_to_paise():
    assert to_paise(0.015) == 0.02
    assert to_paise(4.1706) == 4.17
