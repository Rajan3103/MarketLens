"""Tests for marketlens/signals/pine.py: every generated Pine Script must pass the validator."""
import json
import re

import pytest

from marketlens import config
from marketlens.costs import CostSettings, round_trip_costs
from marketlens.signals import pine
from marketlens.signals import rules as sr
from marketlens.webhook import payload as wh

ALL_KINDS = list(sr.OPERANDS)


# A strategy with one entry condition: `kind` compared with a number or an EMA.
def one_rule(kind: str, op: str = "crosses_above", against: str = "number", **kw) -> sr.SignalStrategy:
    right = {"kind": "number", "value": 50} if against == "number" else {"kind": "ema", "length": 20}
    return sr.SignalStrategy("RELIANCE", entry=[{"left": {"kind": kind}, "op": op, "right": right}], **kw)


# Every indicator, against a number and against another indicator, long and short, daily and intraday.
@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("against", ["number", "ema"])
def test_every_indicator_generates_valid_pine(kind, against):
    for side in ("long", "short"):
        for timeframe in ("1 day", "5 min"):
            code = pine.rules_to_pine(one_rule(kind, against=against, side=side, timeframe=timeframe))
            assert pine.validate_pine(code) == [], code


# Every operator, AND / OR, entry and exit rules, SL / target.
@pytest.mark.parametrize("op", list(sr.OPERATORS))
def test_every_operator(op):
    s = sr.SignalStrategy("TCS", entry=[{"left": {"kind": "ema", "length": 9}, "op": op, "right": {"kind": "ema", "length": 21}},
                                        {"left": {"kind": "rsi"}, "op": ">", "right": {"kind": "number", "value": 55}}],
                          entry_join="OR", exit=[{"left": {"kind": "macd"}, "op": op, "right": {"kind": "macd_signal"}}],
                          sl_pct=2, target_pct=4)
    code = pine.rules_to_pine(s)
    assert pine.validate_pine(code) == []
    assert " or " in code.split("entrySignal = ")[1].splitlines()[0]
    assert {"crosses_above": "ta.crossover(", "crosses_below": "ta.crossunder("}.get(op, f" {op} ") in code


# What the script must contain.
def test_script_contents(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "a-real-secret-that-must-never-appear-1234")
    s = sr.SignalStrategy("INFY", "15 min", entry=[
        {"left": {"kind": "ema", "length": 9}, "op": "crosses_above", "right": {"kind": "ema", "length": 21}},
        {"left": {"kind": "supertrend", "length": 10, "mult": 3}, "op": "<", "right": {"kind": "close"}},
        {"left": {"kind": "bb_lower", "length": 20, "mult": 2}, "op": "<", "right": {"kind": "close"}}],
        sl_pct=1.5, target_pct=3, qty=7, costs=CostSettings(slippage_type="points", slippage_value=0.1))
    code = pine.rules_to_pine(s)
    assert code.splitlines()[0] == "//@version=6"
    assert re.search(r"^strategy\(.*currency=currency\.INR", code, re.M)
    assert "commission_type=strategy.commission.percent" in code and "slippage=2" in code      # 0.1 / 0.05
    for needed in ("input.int(9,", "input.int(21,", "input.float(3,", "input.int(20,", "input.int(7,",
                   "input.float(1.5,", "input.float(3, \"Target", "ta.ema(close", "ta.supertrend(", "ta.bb(close",
                   "strategy.exit(", "strategy.close_all(", "Asia/Kolkata"):
        assert needed in code, needed
    assert code.count("alert(mlJson(") == 3 and "alert.freq_once_per_bar_close" in code
    assert 'alert(mlJson("BUY", "entry rule")' in code and code.count('alert(mlJson("EXIT"') == 2
    assert pine.SECRET_PLACEHOLDER in code and "a-real-secret" not in code
    # The ema(9) used twice is declared once
    assert code.count("ta.ema(close, ema1Len)") == 1


# The alert JSON has exactly the fields the MarketLens webhook checker reads.
def test_alert_json_matches_webhook_format():
    code = pine.rules_to_pine(one_rule("rsi", ">"))
    keys = re.findall(r'\\?"(\w+)\\?":', code.split("mlJson(string action, string note) =>")[1].splitlines()[1])
    assert set(keys) == set(wh.tradingview_message())


# The commission is the costs.py round trip as a % per order.
def test_commission_from_costs():
    expected = round_trip_costs("equity_delivery", 1000, 1000, 100) / (2 * 100_000) * 100
    assert pine.commission_pct(False) == pytest.approx(expected, abs=1e-4)
    assert pine.commission_pct(True) < pine.commission_pct(False)          # intraday is cheaper than delivery


# The old moving-average template still passes, and the validator catches mistakes.
def test_validator():
    assert pine.validate_pine(pine.ma_crossover_script(20, 50)) == []
    good = pine.ma_crossover_script()
    assert any("version=6" in p for p in pine.validate_pine(good.replace("//@version=6", "//@version=5")))
    assert any("never closed" in p for p in pine.validate_pine(good.replace("ta.sma(close, slowLen)", "ta.sma(close, slowLen")))
    assert any("no matching" in p for p in pine.validate_pine(good + "x = 1)\n"))
    assert any("Python" in p for p in pine.validate_pine(good + "def f(x):\n    return x\n"))
    assert any("Python" in p for p in pine.validate_pine(good + "ok = True\n"))
    assert any("'#'" in p for p in pine.validate_pine(good + "# a python comment\n"))
    assert any("':'" in p for p in pine.validate_pine(good + "if close > open:\n    strategy.close(\"Long\")\n"))
    assert any("tabs" in p for p in pine.validate_pine(good + "\tx = 1\n"))
    assert any("not closed" in p for p in pine.validate_pine(good + 'x = "open\n'))
    assert any("strategy" in p for p in pine.validate_pine("//@version=6\nx = 1\n"))
    # Brackets inside strings and comments do not count
    assert pine.validate_pine(good + 'label = "(("\n// ) ] }\n') == []


# Names with quotes cannot break the script.
def test_names_are_safe():
    s = one_rule("ema", name='My "quoted" strategy\\ with a very long name that goes on and on and on and on')
    code = pine.rules_to_pine(s)
    assert pine.validate_pine(code) == [] and '"quoted"' not in code and "..." in code
    json.dumps(code)                                                       # plain text
