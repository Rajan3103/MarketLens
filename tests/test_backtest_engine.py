"""Tests for marketlens/backtest/ engine, charts, report, store and ui."""
import datetime as dt
import io

import pandas as pd
import pytest

from marketlens.backtest import charts
from marketlens.backtest.engine import BacktestResult, Trade, daily_pnl_of, rebase, sample_result
from marketlens.backtest.report import export_excel, export_trades_csv, file_stem, summary_table
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings, round_trip_costs, trade_costs


# A time on a given day (India time).
def at(day: str, hh: int = 9, mm: int = 20) -> dt.datetime:
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(hh, mm), IST)


# One option trade, bought or sold, with the given entry and exit price.
def trade(day: str, side: str = "SELL", entry: float = 100, exit_: float = 80, qty: int = 65, **kw) -> Trade:
    return Trade("NIFTY 25000 CE", side, qty, at(day), entry, at(day, 15, 15), exit_, **kw)


# Three trades over two months plus a third month in the next year.
@pytest.fixture
def result():
    trades = [trade("2025-01-06"), trade("2025-01-06", "BUY", 50, 40, leg_no=2),
              trade("2025-02-04", entry=100, exit_=130), trade("2026-01-07")]
    bench = pd.Series([200.0, 210.0, 190.0, 999.0],
                      index=[dt.date(2024, 12, 31), dt.date(2025, 1, 1), dt.date(2025, 6, 1), dt.date(2026, 6, 1)])
    return BacktestResult.build(trades, 100_000, dt.date(2025, 1, 1), dt.date(2026, 1, 31),
                                name="Test: short CE", benchmark=bench, benchmark_name="NIFTY")


# ---- Trade ----

# Sold at 100, bought back at 80: +1,300 before costs; costs are exactly costs.py's round trip.
def test_trade_costs_from_costs_py():
    t = trade("2025-01-06")
    assert t.gross_pnl == 1300 and t.sign == -1
    assert t.costs == round_trip_costs("options", 100, 80, 65, entry_side="SELL")
    assert t.pnl == pytest.approx(1300 - t.costs)
    slip = CostSettings(slippage_type="points", slippage_value=1)
    assert trade("2025-01-06", settings=slip).costs == round_trip_costs("options", 100, 80, 65, slip, "SELL")
    fut = Trade("NIFTY FUT", "BUY", 65, at("2025-01-06"), 25000, at("2025-01-06", 15), 25100, segment="futures")
    assert fut.gross_pnl == 6500 and fut.costs == round_trip_costs("futures", 25000, 25100, 65)


# Expired worthless (exit price 0): only the opening order has costs.
def test_expired_worthless():
    t = trade("2025-01-06", exit_=0, exit_reason="EXPIRY")
    assert t.costs == trade_costs("options", "SELL", 100, 65).total and t.gross_pnl == 6500


# Bad trades are refused.
@pytest.mark.parametrize("kw", [{"side": "HOLD"}, {"qty": 0}, {"entry": 0}, {"exit_": -1}])
def test_trade_checks(kw):
    with pytest.raises(ValueError):
        trade("2025-01-06", **kw)
    with pytest.raises(ValueError, match="before"):
        Trade("X", "BUY", 1, at("2025-01-06", 15), 10, at("2025-01-06", 9), 11)


# ---- BacktestResult ----

# Two trades on 6-Jan add up to one day; the equity curve starts from the capital.
def test_build(result):
    t = result.trades
    assert [x.exit_time.date() for x in t] == [dt.date(2025, 1, 6)] * 2 + [dt.date(2025, 2, 4), dt.date(2026, 1, 7)]
    assert result.daily_pnl[dt.date(2025, 1, 6)] == pytest.approx(t[0].pnl + t[1].pnl)
    assert result.equity.iloc[-1] == pytest.approx(100_000 + sum(x.pnl for x in t))
    assert result.metrics["trades"] == 4 and result.metrics["total_pnl"] == pytest.approx(sum(x.pnl for x in t))
    assert list(result.daily_frame()["trades"]) == [2, 1, 1]
    assert list(result.trades_frame()["pnl"]) == [x.pnl for x in t]


# Benchmark scaled to the capital, only inside the backtest period.
def test_benchmark_rebased(result):
    assert list(result.benchmark.index) == [dt.date(2025, 1, 1), dt.date(2025, 6, 1)]
    assert list(result.benchmark) == pytest.approx([100_000, 100_000 * 190 / 210])
    assert rebase(None, 1, dt.date(2025, 1, 1), dt.date(2025, 2, 1)) is None


# Bad inputs and an empty backtest.
def test_build_checks():
    with pytest.raises(ValueError):
        BacktestResult.build([], 0, dt.date(2025, 1, 1), dt.date(2025, 2, 1))
    with pytest.raises(ValueError):
        BacktestResult.build([], 1000, dt.date(2025, 2, 1), dt.date(2025, 1, 1))
    empty = BacktestResult.build([], 1000, dt.date(2025, 1, 1), dt.date(2025, 2, 1))
    assert empty.metrics["trades"] == 0 and daily_pnl_of([]).empty and empty.trades_frame().empty
    for make in (charts.equity_chart, charts.drawdown_chart, charts.daily_heatmap,
                 charts.monthly_heatmap, charts.weekday_chart):
        make(empty)


# The DEMO sample is labelled and every trade has costs.
def test_sample_result():
    s = sample_result()
    assert s.is_demo and len(s.trades) == 2 * len(s.daily_pnl) and all(t.costs > 0 for t in s.trades)


# ---- charts ----

# Month table: years down, Jan..Dec across, plus a Total.
def test_monthly_table(result):
    table = charts.monthly_table(result).set_index("Year")
    p = [t.pnl for t in result.trades]
    assert table.loc[2025, "Jan"] == pytest.approx(p[0] + p[1]) and table.loc[2025, "Feb"] == pytest.approx(p[2])
    assert pd.isna(table.loc[2025, "Mar"]) and table.loc[2026, "Total"] == pytest.approx(p[3])


# Weekday table and the calendar squares (6-Jan-2025 is a Monday).
def test_weekday_and_calendar(result):
    week = charts.weekday_table(result).set_index("Weekday")
    assert list(week.index) == ["Mon", "Tue", "Wed"] and week.loc["Tue", "Days"] == 1
    cal = charts.calendar_table(result)
    assert cal.loc[0, "weekday"] == 0 and cal.loc[1, "week"] == dt.date(2025, 2, 3)
    fig = charts.daily_heatmap(result)
    assert fig.data[0].z.shape[0] == 5                        # Mon..Fri rows


# Every chart builds; the equity chart has the benchmark line.
def test_figures(result):
    assert len(charts.equity_chart(result).data) == 2
    assert charts.monthly_heatmap(result).data[0].text[0][0] == charts.short_rs(charts.monthly_table(result).loc[0, "Jan"])
    assert len(charts.weekday_chart(result).data[0].x) == 3
    assert charts.short_rs(125_000) == "1.25L" and charts.short_rs(-4500) == "-4.5k" and charts.short_rs(20_000_000) == "2Cr"


# ---- report ----

# The Excel file has the four sheets, and the numbers match the result.
def test_export_excel(result):
    book = pd.read_excel(io.BytesIO(export_excel(result)), sheet_name=None)
    assert list(book) == ["Summary", "Trades", "Daily P&L", "Monthly P&L"]
    summary = book["Summary"].set_index("Metric")["Value"]
    assert float(summary["Total P&L (Rs)"]) == pytest.approx(result.metrics["total_pnl"], abs=0.01)
    assert "not a live trading recommendation" in summary["Note"]
    assert len(book["Trades"]) == 4 and len(book["Daily P&L"]) == 3 and list(book["Monthly P&L"]["Year"]) == [2025, 2026]
    assert export_trades_csv(result).decode().startswith("leg_no,symbol,side")
    assert file_stem(result) == "Test_short_CE_2025-01-01_2026-01-31"
    assert "Setting: lots" not in set(summary_table(result)["Metric"])


# ---- store (test database only) ----

# Save a run, load it back: same trades, costs and metrics.
def test_save_and_load(db_url, db_session, result):
    from marketlens.backtest.store import list_runs, load_run, save_run
    run_id = save_run(None, {"entry": "09:20"}, result, url=db_url)
    back = load_run(run_id, url=db_url)
    assert back.name == result.name and back.params == {"entry": "09:20"}
    assert [t.pnl for t in back.trades] == [t.pnl for t in result.trades]
    assert [t.costs for t in back.trades] == [t.costs for t in result.trades]
    assert back.metrics == pytest.approx(result.metrics)
    assert list(back.benchmark) == pytest.approx(list(result.benchmark))
    assert list_runs(url=db_url)["id"].tolist() == [run_id]
    with pytest.raises(ValueError):
        load_run(run_id + 99, url=db_url)


# ---- ui ----

# show_backtest draws cards, charts, the trade list, two downloads and the learning note.
def test_show_backtest():
    from streamlit.testing.v1 import AppTest

    def page():
        from marketlens.backtest.engine import sample_result
        from marketlens.backtest.ui import show_backtest
        show_backtest(sample_result())

    at_ = AppTest.from_function(page, default_timeout=60).run()
    assert not at_.exception
    assert "DEMO DATA" in at_.warning[0].value
    labels = [m.label for m in at_.metric]
    assert {"Total P&L", "CAGR", "Max drawdown", "Sharpe", "Sortino", "Calmar", "Win rate",
            "Profit factor", "Expectancy", "Max winning streak", "Max losing streak"} <= set(labels)
    assert any("not a live trading recommendation" in c.value for c in at_.caption)
    assert len(at_.tabs) == 3
