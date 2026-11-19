"""The shared backtest result shape (backtest/present.py), the cost boxes (costs.COST_FIELDS) and the price cache
in marketlens/data.py. Numbers are worked out by hand in the comments. No internet, no database, no Streamlit.
"""
import datetime as dt
import json

import pandas as pd
import pytest

from marketlens import data
from marketlens.backtest.engine import IST, BacktestResult, Trade
from marketlens.backtest.present import METRICS, NOTE, result_to_json
from marketlens.costs import BROKERAGE_CAP_PCT, COST_FIELDS, CostSettings, cost_settings_from_dict


# A closed trade with no costs (so the P&L is easy to check by hand), closing at 15:00 on `day`.
def trade(side, entry, exit_, day):
    when = dt.datetime.combine(day, dt.time(10, 0), IST)
    return Trade("TEST", side, 10, when, entry, when.replace(hour=15), exit_, costs=0.0)


# Three trades on Rs 1,00,000: +1,000 (Jan 2025), -1,000 (Feb 2025), +500 (Mar 2026).
@pytest.fixture
def known():
    trades = [trade("BUY", 100, 200, dt.date(2025, 1, 15)), trade("SELL", 300, 400, dt.date(2025, 2, 10)),
              trade("BUY", 100, 150, dt.date(2026, 3, 2))]
    benchmark = pd.Series([200.0, 220.0], index=[dt.date(2025, 1, 1), dt.date(2026, 12, 31)])
    result = BacktestResult.build(trades, 100_000, dt.date(2025, 1, 1), dt.date(2026, 12, 31), name="Known",
                                  benchmark=benchmark, benchmark_name="NIFTY", params={"lots": 1})
    result.extras["skipped"] = pd.DataFrame([{"date": dt.date(2025, 1, 20), "reason": "holiday"}])
    return result_to_json(result)


# The whole answer is plain JSON (no dates, numpy numbers or NaN left), and says it is historical.
def test_result_is_plain_json(known):
    text = json.dumps(known, allow_nan=False)
    assert '"historical": true' in text
    assert known["note"] == NOTE and known["name"] == "Known" and known["capital"] == 100_000
    assert (known["start"], known["end"], known["is_demo"], known["params"]) == ("2025-01-01", "2026-12-31",
                                                                                False, {"lots": 1})


# The cards: every metric with its label and unit, in the fixed order.
def test_metrics_with_labels_and_units(known):
    cards = {m["key"]: m for m in known["metrics"]}
    assert [m["key"] for m in known["metrics"]] == [k for k, *_ in METRICS]
    assert all(m["label"] and m["unit"] for m in known["metrics"])
    assert cards["total_pnl"]["value"] == 500 and cards["total_pnl"]["unit"] == "Rs"
    assert cards["trades"]["value"] == 3 and cards["total_costs"]["value"] == 0
    assert cards["win_rate_pct"]["value"] == pytest.approx(200 / 3) and cards["win_rate_pct"]["unit"] == "%"


# Equity starts at the capital; the drawdown after the Feb loss is 1,000 below the 1,01,000 high = -0.99 %.
def test_equity_drawdown_and_benchmark(known):
    assert known["equity"] == [{"time": "2025-01-01", "value": 100_000}, {"time": "2025-01-15", "value": 101_000},
                               {"time": "2025-02-10", "value": 100_000}, {"time": "2026-03-02", "value": 100_500}]
    feb = next(p for p in known["drawdown"] if p["time"] == "2025-02-10")
    assert feb["value"] == pytest.approx(-1000 / 101_000 * 100, abs=0.01) and feb["rs"] == pytest.approx(-1000)
    assert known["benchmark_name"] == "NIFTY"
    assert known["benchmark"] == [{"time": "2025-01-01", "value": 100_000}, {"time": "2026-12-31", "value": 110_000}]


# Yearly: 2025 = 0 (win then loss), 2026 = +500 on 1,00,000 = 0.5 %.
# Monthly: Jan +1 % of 1,00,000; Feb -1,000 of 1,01,000 = -0.99 %; Mar 2026 +0.5 %.
def test_yearly_and_monthly_returns(known):
    assert known["yearly_returns"] == [{"year": 2025, "pnl": 0.0, "return_pct": 0.0},
                                       {"year": 2026, "pnl": 500.0, "return_pct": 0.5}]
    assert known["monthly_returns"] == [{"year": 2025, "month": 1, "pnl": 1000.0, "return_pct": 1.0},
                                        {"year": 2025, "month": 2, "pnl": -1000.0, "return_pct": -0.99},
                                        {"year": 2026, "month": 3, "pnl": 500.0, "return_pct": 0.5}]


# Heatmaps are Plotly figures; trades and skipped days are rows.
def test_heatmaps_trades_and_skipped(known):
    assert known["day_heatmap"]["data"][0]["type"] == "heatmap"
    assert known["month_heatmap"]["data"][0]["type"] == "heatmap"
    assert known["trades_total"] == 3 and len(known["trades"]) == 3
    assert [t["pnl"] for t in known["trades"]] == [1000, -1000, 500]
    assert known["trades"][0]["entry_time"].startswith("2025-01-15T10:00:00")
    assert known["skipped_days"] == [{"date": "2025-01-20", "reason": "holiday"}]


# No trades: empty lists, no heatmaps, still marked historical.
def test_result_without_trades():
    empty = result_to_json(BacktestResult.build([], 50_000, dt.date(2025, 1, 1), dt.date(2025, 3, 31)))
    assert empty["trades"] == [] and empty["monthly_returns"] == [] and empty["day_heatmap"] is None
    assert empty["benchmark"] is None and empty["skipped_days"] == [] and empty["historical"] is True
    assert empty["equity"] == [{"time": "2025-01-01", "value": 50_000}]


# ---------------------------------------------------------------- cost boxes

# Nothing sent = the default CostSettings; every box has a label, a kind and a default.
def test_cost_defaults():
    assert cost_settings_from_dict({}) == CostSettings() == cost_settings_from_dict(None)
    assert {f["name"] for f in COST_FIELDS} == {"brokerage_per_order", "brokerage_cap", "delivery_brokerage",
                                                "slippage_type", "slippage_value", "include_taxes"}
    assert all(f["label"] and f["kind"] in ("number", "bool", "choice") and "default" in f for f in COST_FIELDS)


# Values as the web app sends them (JSON numbers, text, booleans) become the same CostSettings.
def test_cost_settings_from_dict():
    s = cost_settings_from_dict({"brokerage_per_order": "15", "brokerage_cap": False, "slippage_type": "points",
                                 "slippage_value": 0.5, "include_taxes": "false"})
    assert s == CostSettings(brokerage_per_order=15.0, brokerage_cap_pct=None, slippage_type="points",
                             slippage_value=0.5, include_taxes=False)
    assert cost_settings_from_dict({"brokerage_cap": "yes"}).brokerage_cap_pct == BROKERAGE_CAP_PCT


# Bad values are refused with a plain message.
@pytest.mark.parametrize("values, words", [
    ({"brokerage_per_order": -1}, "cannot be below"), ({"slippage_value": "abc"}, "must be a number"),
    ({"slippage_type": "bps"}, "must be one of"), ({"include_taxes": "maybe"}, "true or false"),
    ({"brokerage": 20}, "Unknown cost setting"), ({"slippage_value": float("nan")}, "cannot be below")])
def test_cost_settings_refused(values, words):
    with pytest.raises(ValueError, match=words):
        cost_settings_from_dict(values)


# ---------------------------------------------------------------- price cache (no Streamlit)

# The second call within 60 s uses the remembered table, and changing the answer never changes the cache.
def test_history_cache(monkeypatch):
    calls = []

    def fake(ticker, period="1y", interval="1d"):
        calls.append(ticker)
        return pd.DataFrame({"Close": [1.0, 2.0]})
    monkeypatch.setattr(data, "download_history", fake)
    first = data.get_history("RELIANCE.NS")
    first.loc[0, "Close"] = 99.0
    assert data.get_history("RELIANCE.NS")["Close"].tolist() == [1.0, 2.0] and calls == ["RELIANCE.NS"]
    data.get_history("TCS.NS")
    assert calls == ["RELIANCE.NS", "TCS.NS"]
    assert data.SHORT_SECONDS == 60 and data.LONG_SECONDS == 3600
