"""Tests for marketlens/signals/rules.py and backtest.py (module 7), with candles you can check by hand.

Daily candles (open, high, low, close); rules: enter when close crosses above 100, exit when it
crosses below 100. A rule true at a close fills at the NEXT candle's open.

    day 0   95  96  94  95
    day 1   96  97  95  96
    day 2   99 102  98 101   <- close crosses above 100: buy at day 3's open (102)
    day 3  102 106 101 105
    day 4  105 110 104 108
    day 5  108 109 100 103
    day 6  103 104  97  99   <- close crosses below 100: sell at day 7's open (98)
    day 7   98  99  96  97
    day 8   97  98  95  96
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketlens.backtest.engine import order_costs
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings
from marketlens.signals import backtest as sb
from marketlens.signals import indicators as ind
from marketlens.signals import rules as sr

NO_COSTS = CostSettings(brokerage_per_order=0, include_taxes=False)
BARS = [(95, 96, 94, 95), (96, 97, 95, 96), (99, 102, 98, 101), (102, 106, 101, 105), (105, 110, 104, 108),
        (108, 109, 100, 103), (103, 104, 97, 99), (98, 99, 96, 97), (97, 98, 95, 96)]
START = dt.date(2026, 1, 5)
CROSS_UP = [{"left": {"kind": "close"}, "op": "crosses_above", "right": {"kind": "number", "value": 100}}]
CROSS_DOWN = [{"left": {"kind": "close"}, "op": "crosses_below", "right": {"kind": "number", "value": 100}}]


# Daily candles from (open, high, low, close) tuples, one per weekday.
def candles(bars=BARS, start=START, minutes=None, per_day=None) -> pd.DataFrame:
    if minutes:
        times, day = [], pd.Timestamp(start)
        for n in range(len(bars)):
            d = day + pd.offsets.BDay(n // per_day)
            times.append(pd.Timestamp(dt.datetime.combine(d.date(), dt.time(9, 15))) + pd.Timedelta(minutes=minutes * (n % per_day)))
    else:
        times = list(pd.bdate_range(start, periods=len(bars)))
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=pd.DatetimeIndex(times))
    df["volume"] = 1000
    return sb.tidy(df)


# The hand-made strategy (long, 10 shares, no costs unless given).
def strategy(**kw) -> sr.SignalStrategy:
    base = dict(symbol="TEST", start=START, end=START + dt.timedelta(days=30), entry=CROSS_UP, exit=CROSS_DOWN,
                qty=10, costs=NO_COSTS, capital=100_000)
    return sr.SignalStrategy(**{**base, **kw})


# ---- operands and conditions ----

def test_operands_match_indicators():
    c = candles([(p, p + 1, p - 1, p) for p in np.linspace(100, 140, 60)])
    assert sr.Operand("ema", length=9).series(c).equals(ind.ema(c["close"], 9))
    assert sr.Operand("bb_upper", length=20, mult=2).series(c).equals(ind.bollinger(c["close"], 20, 2)["upper"])
    assert sr.Operand("macd_hist").series(c).equals(ind.macd(c["close"], 12, 26, 9)["hist"])
    assert sr.Operand("number", value=50).series(c).eq(50).all()
    assert sr.Operand("macd").label() == "MACD line(12,26,9)" and sr.Operand("rsi", length=7).label() == "RSI(7)"
    with pytest.raises(ValueError):
        sr.Operand("magic")
    with pytest.raises(ValueError):
        sr.Operand("macd", fast=26, slow=12)


def test_conditions_and_joins():
    c = candles()
    up = sr.Condition(**CROSS_UP[0]).evaluate(c)
    assert list(up) == [False, False, True] + [False] * 6
    above = sr.Condition(sr.Operand("close"), ">", sr.Operand("number", value=100)).evaluate(c)
    assert list(above) == [False, False, True, True, True, True, False, False, False]
    down = sr.Condition(**CROSS_DOWN[0])
    both = sr.evaluate([sr.Condition(**CROSS_UP[0]), down], "OR", c)
    assert list(np.flatnonzero(both)) == [2, 6]
    assert not sr.evaluate([sr.Condition(**CROSS_UP[0]), down], "AND", c).any()
    assert sr.Condition(**CROSS_UP[0]).label() == "Close crosses above 100"


# A rule at candle i uses only candles up to i: cutting the data later never changes earlier answers.
def test_rules_never_look_ahead():
    rng = np.random.default_rng(4)
    closes = 100 + rng.normal(0, 1, 120).cumsum()
    c = candles([(x, x + 1, x - 1, x) for x in closes])
    conds = [sr.Condition(sr.Operand("ema", length=9), "crosses_above", sr.Operand("ema", length=21)),
             sr.Condition(sr.Operand("rsi"), ">", sr.Operand("number", value=50)),
             sr.Condition(sr.Operand("macd"), ">", sr.Operand("macd_signal")),
             sr.Condition(sr.Operand("close"), "<", sr.Operand("bb_lower")),
             sr.Condition(sr.Operand("close"), ">", sr.Operand("supertrend"))]
    full = {n: cond.evaluate(c) for n, cond in enumerate(conds)}
    for cut in (40, 77, 100):
        for n, cond in enumerate(conds):
            assert cond.evaluate(c.iloc[:cut]).equals(full[n].iloc[:cut])


# ---- the backtest, by hand ----

def test_entry_and_exit_at_next_open():
    result, markers = sb.run_signal_backtest(strategy(), candles())
    t = result.trades[0]
    assert (t.entry_price, t.exit_price, t.gross_pnl, t.exit_reason) == (102, 98, -40, "SIGNAL")
    assert t.entry_time.date() == dt.date(2026, 1, 8) and t.exit_time.date() == dt.date(2026, 1, 14)
    assert list(markers["kind"]) == ["entry", "exit"] and len(result.trades) == 1


def test_short_side():
    t = sb.run_signal_backtest(strategy(side="short"), candles())[0].trades[0]
    assert (t.side, t.entry_price, t.exit_price, t.gross_pnl) == ("SELL", 102, 98, 40)


# Target 5% of 102 = 107.10: day 4 reaches 110 -> out at 107.10. SL 3% = 98.94: day 6 falls to 97 -> out at 98.94.
def test_sl_and_target():
    t = sb.run_signal_backtest(strategy(target_pct=5), candles())[0].trades[0]
    assert (t.exit_reason, t.exit_price) == ("TARGET", pytest.approx(107.1)) and t.exit_time.date() == dt.date(2026, 1, 9)
    t = sb.run_signal_backtest(strategy(sl_pct=3), candles())[0].trades[0]
    assert (t.exit_reason, t.exit_price) == ("SL", pytest.approx(98.94))
    # SL 0.5% (101.49) and target 3% (105.06) both inside day 3 (low 101, high 106): the SL is assumed
    t = sb.run_signal_backtest(strategy(sl_pct=0.5, target_pct=3), candles())[0].trades[0]
    assert t.exit_reason == "SL" and t.note == sb.BOTH_HIT


# Costs come from costs.py (delivery for daily candles).
def test_costs_from_costs_py():
    t = sb.run_signal_backtest(strategy(costs=CostSettings()), candles())[0].trades[0]
    assert t.costs == order_costs("equity_delivery", "BUY", 102, 98, 10, CostSettings())


# Intraday: closed at the day's last candle; no entry on a day's last candle.
def test_intraday_day_end():
    bars = [(95, 96, 94, 95), (99, 102, 98, 101), (102, 104, 101, 103), (103, 105, 102, 104),     # day 1
            (104, 105, 99, 99), (99, 101, 98, 101), (101, 103, 100, 102), (102, 103, 101, 102)]   # day 2
    s = strategy(timeframe="5 min", exit=[])
    result, _ = sb.run_signal_backtest(s, candles(bars, minutes=5, per_day=4))
    first, second = result.trades
    assert (first.entry_price, first.exit_price, first.exit_reason) == (102, 104, "DAY_END")
    assert first.exit_time.time() == dt.time(9, 35)                    # the 09:30 candle closed at 09:35
    assert (second.entry_price, second.exit_reason) == (101, "DAY_END")
    assert segment_of(first) == "equity_intraday"


# The cost segment a trade used.
def segment_of(trade):
    return trade.segment


# Settings checks, JSON round trip and the operands list.
def test_strategy_settings():
    s = strategy(entry=CROSS_UP + [{"left": {"kind": "rsi"}, "op": ">", "right": {"kind": "number", "value": 50}}])
    assert sr.SignalStrategy.from_params(s.to_params()) == s
    assert [o.label() for o in s.operands()] == ["RSI(14)"]
    assert "Close crosses above 100 AND RSI(14) > 50" in s.name
    for bad in ({"entry": []}, {"timeframe": "2 min"}, {"side": "up"}, {"sl_pct": -1}, {"qty": 0}, {"symbol": " "}):
        with pytest.raises(ValueError):
            strategy(**bad)
    with pytest.raises(ValueError):
        sb.run_signal_backtest(strategy(), candles(BARS[:1]))


# The chart has candles, the indicators and the markers.
def test_chart():
    s = strategy(entry=[{"left": {"kind": "ema", "length": 3}, "op": "crosses_above", "right": {"kind": "sma", "length": 5}},
                        {"left": {"kind": "rsi", "length": 3}, "op": ">", "right": {"kind": "number", "value": 40}}])
    c = candles(BARS * 3)
    _, markers = sb.run_signal_backtest(s, c)
    names = [t.name for t in sb.signal_chart(s, c, markers).data]
    assert names[:4] == ["TEST", "EMA(3)", "SMA(5)", "RSI(3)"]


# ---- scanner ----

def test_scan():
    up = candles(BARS[:3])                                  # last close crosses above 100 -> signal
    flat = candles(BARS[3:6])
    result = sb.scan(strategy(), ["AAA", "BBB", "CCC"], candles={"AAA": up, "BBB": flat})
    assert list(result["symbol"]) == ["AAA", "BBB", "CCC"]
    assert list(result["signal"].fillna("none")) == [True, False, "none"]
    assert result.loc[0, "close"] == 101 and result.loc[2, "note"] == "no data"


# ---- loading candles ----

def test_load_candles_falls_back_to_demo():
    from marketlens.broker.demo_data import DemoDataClient
    c = sb.load_candles("RELIANCE", "15 min", dt.date(2026, 10, 26), dt.date(2026, 10, 27),
                        client=DemoDataClient(spots={"RELIANCE": 1400}, vix=14))
    assert c.attrs["source"] == "DEMO" and list(c.columns) == sb.CANDLE_COLUMNS and len(c) == 2 * 25
    assert str(c.index.tz) == str(IST)


# ---- Strategy Library (test database) ----

def test_save_and_list(db_url, db_session):
    s = strategy()
    sid = sr.save_signal_strategy("Cross 100", s, url=db_url)
    assert sr.save_signal_strategy("Cross 100", strategy(qty=5), url=db_url) == sid
    saved = sr.list_signal_strategies(url=db_url)
    assert list(saved["name"]) == ["Cross 100"]
    assert sr.SignalStrategy.from_params(saved["params"].iloc[0]).qty == 5
