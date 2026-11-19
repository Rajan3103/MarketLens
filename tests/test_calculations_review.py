"""Extra calculation tests added in the backend review (Prompt 42). Each number is worked out by hand in a comment.

Areas: returns, crossover signals, option payoff, strike selection, trailing stop rules and costs.
No internet and no database.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketlens import costs
from marketlens.costs import CostSettings, round_trip_costs, to_paise, trade_costs
from marketlens.data import add_moving_averages
from marketlens.momentum.portfolio import curve_metrics, yearly_returns
from marketlens.options.strategies import Leg, breakevens, max_loss, max_profit, payoff
from marketlens.options.strike_select import atm, atm_offset_strike, offset_of, strike_step
from marketlens.backtest.options_bt import LegSpec
from tests.test_options_bt_rules import CE, run, t

EXPIRY = dt.date(2026, 11, 24)


# ---------------------------------------------------------------- returns

# Year by year: 100 -> 110 (+10 %) -> 99 (-10 %); the first year is measured from the starting capital.
def test_yearly_returns_by_hand():
    curve = pd.Series([105.0, 110.0, 104.5, 99.0],
                      index=[dt.date(2024, 6, 28), dt.date(2024, 12, 31), dt.date(2025, 6, 30), dt.date(2025, 12, 31)])
    assert yearly_returns(curve, 100.0).to_dict() == {2024: 10.0, 2025: -10.0}


# Total return, CAGR and drawdown from a value curve: Rs 1 lakh -> 1.21 lakh in 2 years = 10 % a year.
def test_curve_metrics_by_hand():
    days = pd.date_range("2024-01-01", "2026-01-01", freq="D").date
    curve = pd.Series(np.linspace(100_000, 121_000, len(days)), index=days)
    curve.iloc[100] = curve.iloc[99] * 0.9                     # one 10 % dip from a new high
    m = curve_metrics(curve, 100_000, days[0], days[-1])
    assert m["total_return_pct"] == pytest.approx(21.0)
    assert m["cagr_pct"] == pytest.approx(10.0, abs=0.02)      # (1.21) ** (1 / 2 years) - 1
    assert m["max_drawdown_pct"] == pytest.approx(-10.0, abs=0.01)
    assert curve_metrics(pd.Series(dtype=float), 100_000, days[0], days[-1]) == {}


# ---------------------------------------------------------------- crossover signals

# Close prices with a 2-day and a 3-day average.
def crosses(closes):
    df = pd.DataFrame({"Close": closes}, index=pd.date_range("2026-01-01", periods=len(closes)), dtype=float)
    return add_moving_averages(df, fast=2, slow=3)["Signal"].tolist()


# No signal while the slow average is still warming up, even if the fast one is "above".
def test_no_signal_during_warm_up():
    assert crosses([10, 11, 12, 13]) == [0, 0, 0, 0]


# A rise, a fall and a rise again give +1, -1, +1 in order (never two of the same in a row).
def test_signals_alternate():
    signals = crosses([10, 9, 8, 7, 8, 9, 10, 9, 8, 7, 8, 9, 10])
    fired = [s for s in signals if s]
    assert fired == [1, -1, 1]


# Equal averages are "not above": fast == slow then fast > slow is one bullish cross, not two.
def test_tie_then_rise_is_one_cross():
    # MA2: -, 10, 10, 10.5 ... MA3: -, -, 10, 10.33 -> equal on day 2, above on day 3
    signals = crosses([10, 10, 10, 11, 12])
    assert signals.count(1) == 1 and signals.count(-1) == 0


# ---------------------------------------------------------------- option payoff at expiry

# One leg at lot size 65.
def leg(side, kind, strike, price, lots=1):
    return Leg(side, kind, strike, EXPIRY, lots=lots, entry_price=price, lot_size=65)


# Bull call spread: buy 25000 CE @120, sell 25200 CE @50 (net debit 70).
def test_bull_call_spread_by_hand():
    legs = [leg("BUY", "CE", 25000, 120), leg("SELL", "CE", 25200, 50)]
    prices = np.arange(24800, 25401, 1.0)
    pnl = payoff(legs, prices, at="expiry")
    at = dict(zip(prices, pnl))
    assert at[24800] == pytest.approx(-70 * 65)               # both expire worthless: lose the debit
    assert at[25300] == pytest.approx((200 - 70) * 65)        # spread width 200 minus the debit
    assert breakevens(prices, pnl) == [25070.0]               # lower strike + debit
    assert max_profit(legs, prices) == pytest.approx(130 * 65) and max_loss(legs, prices) == pytest.approx(-70 * 65)


# Iron butterfly: sell 25000 CE @150 and PE @140, buy 25200 CE @60 and 24800 PE @55 (net credit 175).
def test_iron_butterfly_by_hand():
    legs = [leg("SELL", "CE", 25000, 150), leg("SELL", "PE", 25000, 140),
            leg("BUY", "CE", 25200, 60), leg("BUY", "PE", 24800, 55)]
    prices = np.arange(24500, 25501, 1.0)
    pnl = payoff(legs, prices, at="expiry")
    assert dict(zip(prices, pnl))[25000] == pytest.approx(175 * 65)          # the whole credit
    assert breakevens(prices, pnl) == [24825.0, 25175.0]                     # 25000 -/+ 175
    assert max_loss(legs, prices) == pytest.approx(-(200 - 175) * 65)        # wing width minus the credit


# A 1x2 call ratio spread has no limit to its loss on a big rally.
def test_ratio_spread_loss_is_unlimited():
    legs = [leg("BUY", "CE", 25000, 100), leg("SELL", "CE", 25200, 40, lots=2)]
    assert max_loss(legs, np.arange(24500, 26001, 5.0)) is None


# ---------------------------------------------------------------- strike selection

# ATM is the nearest strike even when the list is unsorted or the spot is outside the listed strikes.
def test_atm_edges():
    strikes = [25100.0, 24900, 25000, 24800, 25200]
    assert strike_step(strikes) == 100 and atm(strikes, 25049) == 25000 and atm(strikes, 25051) == 25100
    assert atm(strikes, 26000) == 25200 and atm(strikes, 24000) == 24800
    assert atm_offset_strike(strikes, 25051, -2) == 24900 and offset_of(25200, strikes, 25051) == 1


# ---------------------------------------------------------------- trailing stop rules (options backtester)

# Sold at 100, SL 120, trail every 10 by 5: a fall to 79 moves the SL to 110. The price coming back up must
# NOT move the SL up again (a trailing stop only tightens): high 112 later -> out at 110.
def test_trailing_stop_never_loosens():
    _, tr = run([LegSpec("SELL", "CE", unit="points", sl=20, trail_x=10, trail_y=5)],
                {CE: [100, 100, (100, 100, 79, 80), (80, 105, 80, 104), (104, 112, 103, 108)]})
    row = tr.iloc[0]
    assert (row.exit_price, row.exit_reason, row.exit_time, row.gross_pnl) == (110, "TRAIL_SL", t(4), -100)


# A bought leg trails upwards: bought at 100, SL 80; a rise to 121 moves the SL to 90; a dip to 89 -> out at 90.
def test_trailing_stop_for_a_bought_leg():
    _, tr = run([LegSpec("BUY", "CE", unit="points", sl=20, trail_x=10, trail_y=5)],
                {CE: [100, 100, (100, 121, 100, 118), (118, 119, 89, 95)]})
    row = tr.iloc[0]
    assert (row.exit_price, row.exit_reason, row.gross_pnl) == (90, "TRAIL_SL", -100)


# ---------------------------------------------------------------- costs

# ETF sale: STT only on equity ETFs (0.001 %), none on gold / liquid ETFs; DP charge only when selling.
def test_etf_costs_by_hand():
    s = CostSettings()
    sell_equity = trade_costs("etf_equity", "SELL", 250.0, 400, s)          # turnover Rs 1,00,000
    sell_gold = trade_costs("etf_other", "SELL", 250.0, 400, s)
    buy_equity = trade_costs("etf_equity", "BUY", 250.0, 400, s)
    assert sell_equity.stt == to_paise(100_000 * costs.STT_ETF_EQUITY_SELL_PCT / 100) == 1.0
    assert sell_gold.stt == 0 and buy_equity.stt == 0
    assert sell_equity.dp == costs.DP_CHARGE_RS and buy_equity.dp == 0
    assert buy_equity.stamp == to_paise(100_000 * costs.STAMP_DELIVERY_BUY_PCT / 100) == 15.0
    # GST = 18 % of (brokerage + exchange + SEBI + DP)
    expected_gst = (sell_equity.brokerage + 100_000 * costs.EXCHANGE_EQUITY_PCT / 100
                    + 100_000 * costs.SEBI_FEE_PCT / 100 + costs.DP_CHARGE_RS) * 0.18
    assert sell_equity.gst == pytest.approx(to_paise(expected_gst), abs=0.01)


# A round trip is exactly the buy costs plus the sell costs.
def test_round_trip_is_buy_plus_sell():
    s = CostSettings()
    both = round_trip_costs("options", 100.0, 120.0, 65, s)
    one, two = trade_costs("options", "BUY", 100.0, 65, s), trade_costs("options", "SELL", 120.0, 65, s)
    assert both == pytest.approx(to_paise(one.total + two.total), abs=0.02)


# ---------------------------------------------------------------- bugs found by the no-keys check

# A very low spot with NIFTY's fixed 50-point gap used to make strikes below 0 (math domain error).
def test_demo_chain_never_has_negative_strikes():
    from marketlens.broker.candles import IST
    from marketlens.broker.demo_data import DemoDataClient
    client = DemoDataClient(spots={"NIFTY": 260}, vix=14, now=dt.datetime(2026, 10, 28, 11, tzinfo=IST))
    table = client.get_option_chain("NIFTY", dt.date(2026, 11, 3)).table
    assert len(table) > 0 and (table["strike"] > 0).all()


# When Yahoo has no price for any index, the cards table still holds numbers (NaN), never None.
def test_overview_table_is_numeric_when_every_price_is_missing():
    from marketlens.overview import _table
    table = _table([("NIFTY", "Nifty 50", None, None), ("SENSEX", "Sensex", None, None)])
    assert table[["last", "prev_close", "change", "pct_change"]].dtypes.eq(float).all()
    assert table["last"].isna().all()
