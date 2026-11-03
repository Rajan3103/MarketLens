"""Tests for marketlens/library.py (module 10).

The table functions are tested with hand-made rows; the save / load functions use TEST_DATABASE_URL
(skipped without it) and never the real database.
"""
import datetime as dt
import json

import pandas as pd
import pytest
from sqlalchemy import func, select

from marketlens import library as lib
from marketlens.backtest.options_bt import save_backtest_strategy, template_settings
from marketlens.broker.candles import IST
from marketlens.models import (BacktestRun, ForwardTestRun, HoldingsSnapshot, Instrument, Order, SimulatorSession,
                               SimulatorTrade, Strategy, StrategyLeg)

T0 = dt.datetime(2026, 10, 26, 9, 20, tzinfo=IST)


# One filled order row in the layout load_orders() returns.
def fill(id_, side, qty, price, minutes=0, costs=0.0, symbol="RELIANCE", strategy_id=1, mode="paper", forward=None):
    return {"id": id_, "mode": mode, "strategy_id": strategy_id, "strategy": "Swing" if strategy_id else None,
            "forward_test_run_id": forward, "symbol": symbol, "product": "CNC", "side": side, "qty": qty,
            "price": price, "costs": costs, "time": T0 + dt.timedelta(minutes=minutes)}


# ---------------------------------------------------------------- round trips

def test_round_trip_average_price_and_costs():
    orders = pd.DataFrame([fill(1, "BUY", 10, 100, 0, 2.0), fill(2, "BUY", 10, 110, 5, 2.0),
                           fill(3, "SELL", 20, 120, 60, 4.0)])
    t = lib.round_trips(orders).iloc[0]
    assert t["key"] == "order:1" and t["status"] == "closed" and t["side"] == "BUY"
    assert t["qty"] == 20 and t["entry_price"] == 105 and t["exit_price"] == 120
    assert t["gross_pnl"] == 300.00 and t["costs"] == 8.00 and t["net_pnl"] == 292.00
    assert t["source"] == "paper" and t["date"] == T0.date()


def test_round_trip_flip_short_and_partial():
    orders = pd.DataFrame([fill(1, "BUY", 5, 100, 0, 1.0), fill(2, "SELL", 8, 90, 10, 1.6),     # closes 5, shorts 3
                           fill(3, "BUY", 1, 80, 20, 0.0)])                                     # covers 1 of 3
    trades = lib.round_trips(orders)
    first, second = trades.iloc[0], trades.iloc[1]
    assert first["gross_pnl"] == -50.00 and first["costs"] == 2.00 and first["net_pnl"] == -52.00
    assert second["side"] == "SELL" and second["status"] == "open" and second["entry_price"] == 90
    assert second["key"] == "order:2" and pd.isna(second["net_pnl"])


def test_round_trips_are_kept_apart_by_strategy_mode_and_source():
    orders = pd.DataFrame([fill(1, "BUY", 1, 10, 0, strategy_id=1), fill(2, "BUY", 1, 10, 0, strategy_id=2),
                           fill(3, "SELL", 1, 12, 5, strategy_id=1, forward=7), fill(4, "SELL", 1, 9, 5, strategy_id=2),
                           fill(5, "BUY", 1, 10, 0, strategy_id=None, mode="live"),
                           fill(6, "SELL", 1, 11, 9, strategy_id=None, mode="live")])
    trades = lib.round_trips(orders).set_index("key")
    assert trades.loc["order:1", "net_pnl"] == 2 and trades.loc["order:1", "source"] == "forward"
    assert trades.loc["order:2", "net_pnl"] == -1 and trades.loc["order:2", "source"] == "paper"
    assert trades.loc["order:5", "source"] == "live" and trades.loc["order:5", "strategy"] == "(no strategy)"


def test_simulator_pairs_open_and_close():
    rows = pd.DataFrame([
        {"id": 1, "session_id": 3, "symbol": "NIFTY 25000 CE", "side": "SELL", "qty": 65, "price": 120.0,
         "market_time": T0, "pnl": None, "costs": 20.0, "reason": "OPEN"},
        {"id": 2, "session_id": 3, "symbol": "NIFTY 25000 PE", "side": "SELL", "qty": 65, "price": 110.0,
         "market_time": T0, "pnl": None, "costs": 20.0, "reason": "OPEN"},
        {"id": 3, "session_id": 3, "symbol": "NIFTY 25000 CE", "side": "BUY", "qty": 65, "price": 100.0,
         "market_time": T0 + dt.timedelta(hours=2), "pnl": 1255.0, "costs": 25.0, "reason": "SL"}])
    trades = lib.simulator_trades(rows).set_index("key")
    ce = trades.loc["sim:1"]
    assert ce["net_pnl"] == 1255.0 and ce["costs"] == 45.0 and ce["gross_pnl"] == 1300.0
    assert ce["source"] == "simulator" and ce["strategy"] == "Simulator session 3"
    assert trades.loc["sim:2", "status"] == "open"


# ---------------------------------------------------------------- journal tables

# Three closed trades and one open one, as journal_table() returns them.
def journal_rows() -> pd.DataFrame:
    trades = pd.DataFrame([
        {"key": "a", "source": "paper", "strategy": "S1", "symbol": "RELIANCE", "status": "closed", "net_pnl": 500.0,
         "date": dt.date(2026, 10, 26)},                                                   # Monday
        {"key": "b", "source": "forward", "strategy": "S1", "symbol": "TCS", "status": "closed", "net_pnl": -200.0,
         "date": dt.date(2026, 10, 27)},                                                   # Tuesday
        {"key": "c", "source": "simulator", "strategy": "S2", "symbol": "NIFTY 25000 CE", "status": "closed",
         "net_pnl": 100.0, "date": dt.date(2026, 10, 26)},
        {"key": "d", "source": "paper", "strategy": "S2", "symbol": "INFY", "status": "open", "net_pnl": None,
         "date": dt.date(2026, 10, 28)}])
    journal = pd.DataFrame([{"key": "a", "notes": "good", "tags": ["setup:Breakout", "emotion:Calm"], "rating": 5,
                             "screenshot": None},
                            {"key": "b", "notes": None, "tags": ["setup:Breakout", "mistake:Late exit"], "rating": 2,
                             "screenshot": None}])
    return lib.journal_table(trades, journal)


def test_tags_round_trip():
    tags = lib.make_tags(["Breakout"], ["Late exit", " "], ["Fear", "Fear"])
    assert tags == ["setup:Breakout", "mistake:Late exit", "emotion:Fear"]
    assert lib.split_tags(tags + ["own"]) == {"setup": ["Breakout"], "mistake": ["Late exit"],
                                              "emotion": ["Fear"], "other": ["own"]}


def test_stats_by_tag_weekday_strategy():
    table = journal_rows()
    tags = lib.stats_by(table, "tag").set_index("Tag")
    assert tags.loc["setup:Breakout", "Trades"] == 2 and tags.loc["setup:Breakout", "Win rate %"] == 50.0
    assert tags.loc["setup:Breakout", "Net P&L"] == 300.0 and tags.loc["mistake:Late exit", "Net P&L"] == -200.0
    days = lib.stats_by(table, "weekday")
    assert days["Weekday"].tolist() == ["Mon", "Tue"] and days.iloc[0]["Trades"] == 2
    strategies = lib.stats_by(table, "strategy").set_index("Strategy")
    assert strategies.loc["S1", "Net P&L"] == 300.0 and strategies.loc["S2", "Trades"] == 1     # open trade left out
    with pytest.raises(ValueError):
        lib.stats_by(table, "colour")


def test_filters():
    table = journal_rows()
    assert lib.filter_trades(table, sources=["paper"])["key"].tolist() == ["a", "d"]
    assert lib.filter_trades(table, result="win")["key"].tolist() == ["a", "c"]
    assert lib.filter_trades(table, result="loss")["key"].tolist() == ["b"]
    assert lib.filter_trades(table, result="open")["key"].tolist() == ["d"]
    assert lib.filter_trades(table, tags=["mistake:Late exit"])["key"].tolist() == ["b"]
    assert lib.filter_trades(table, symbol="nifty")["key"].tolist() == ["c"]
    assert lib.filter_trades(table, start=dt.date(2026, 10, 27), strategies=["S1"])["key"].tolist() == ["b"]


def test_screenshot_checks(tmp_path):
    path = lib.save_screenshot("order:12", "chart.PNG", b"\x89PNG data", folder=tmp_path)
    assert path.endswith(".png") and "order_12_" in path and open(path, "rb").read() == b"\x89PNG data"
    with pytest.raises(ValueError, match="image"):
        lib.save_screenshot("order:12", "notes.exe", b"x", folder=tmp_path)
    with pytest.raises(ValueError, match="MB"):
        lib.save_screenshot("order:12", "big.png", b"0" * (lib.MAX_SCREENSHOT_MB * 1024 * 1024 + 1), folder=tmp_path)


# ---------------------------------------------------------------- holdings

def test_holdings_view_by_hand():
    rows = pd.DataFrame([
        {"symbol": "RELIANCE", "kind": "Holding", "qty": 10, "avg_price": 1200.0, "ltp": 1300.0, "prev_close": 1250.0,
         "realized_pnl": 0.0, "opened_today": False, "strategy": "x", "source": "PAPER"},
        {"symbol": "NIFTY 25000 CE 03-Nov-2026", "kind": "Position", "qty": -65, "avg_price": 100.0, "ltp": 80.0,
         "prev_close": 150.0, "realized_pnl": -40.0, "opened_today": True, "strategy": "x", "source": "PAPER"}])
    view = lib.holdings_view(rows).set_index("symbol")
    rel, opt = view.loc["RELIANCE"], view.loc["NIFTY 25000 CE 03-Nov-2026"]
    assert rel["invested"] == 12_000 and rel["current_value"] == 13_000 and rel["open_pnl"] == 1_000
    assert rel["day_pnl"] == 500 and rel["return_pct"] == pytest.approx(8.33, abs=0.01)
    assert opt["open_pnl"] == 1_300 and opt["day_pnl"] == 1_300            # opened today: from the sell price
    assert opt["total_pnl"] == 1_260 and opt["sector"] == "Index options"
    assert rel["sector"] != "Other"                                      # from the Nifty 200 list
    totals = lib.holdings_totals(lib.holdings_view(rows))
    assert totals["invested"] == 18_500 and totals["total_pnl"] == 2_260
    shares = lib.allocation(lib.holdings_view(rows))
    assert shares["Share %"].sum() == pytest.approx(100)


def test_sector_names():
    assert lib.sector_of("NIFTYBEES").startswith("ETF")
    assert lib.sector_of("BANKNIFTY 52000 PE 25-Nov-2026") == "Index options"
    assert lib.sector_of("RELIANCE 1400 CE 25-Nov-2026") == "Stock options"
    assert lib.sector_of("NIFTY NOV FUT") == "Index futures"
    assert lib.sector_of("NOTAREALSTOCK") == "Other"


def test_dhan_rows_and_quotes():
    holdings = pd.DataFrame([{"tradingSymbol": "TCS", "securityId": "11536", "totalQty": 5, "avgCostPrice": 3500.0}])
    positions = pd.DataFrame([
        {"tradingSymbol": "INFY", "netQty": -10, "buyAvg": 0, "sellAvg": 1500.0, "costPrice": 0,
         "realizedProfit": 0, "productType": "INTRADAY"},
        {"tradingSymbol": "SBIN", "netQty": 0, "buyAvg": 800, "sellAvg": 810, "costPrice": 0,
         "realizedProfit": 100.0, "productType": "INTRADAY"},
        {"tradingSymbol": "ITC", "netQty": 0, "buyAvg": 400, "sellAvg": 400, "costPrice": 0, "realizedProfit": 0,
         "productType": "INTRADAY"}])
    rows = lib.dhan_rows(holdings, positions)
    assert rows["symbol"].tolist() == ["TCS", "INFY", "SBIN"]            # ITC: closed with no P&L
    assert rows.set_index("symbol").loc["INFY", "avg_price"] == 1500.0

    # A quote function that knows TCS and INFY and fails for SBIN.
    def quote(symbols):
        prices = {"TCS": (3600.0, 3550.0), "INFY": (1490.0, 1480.0)}
        if symbols[0] not in prices:
            raise ValueError("unknown")
        ltp, close = prices[symbols[0]]
        return pd.DataFrame([{"symbol": symbols[0], "ltp": ltp, "close": close}])

    view = lib.holdings_view(lib.add_quotes(rows, quote)).set_index("symbol")
    assert view.loc["TCS", "day_pnl"] == 250 and view.loc["INFY", "open_pnl"] == 100
    assert view.loc["SBIN", "total_pnl"] == 100                          # realised only
    assert view.loc["SBIN", "day_pnl"] == 0                             # opened today, closed: no open qty
    no_close = lib.holdings_view(rows.iloc[:1]).iloc[0]                  # TCS without a quote: day P&L unknown
    assert pd.isna(no_close["day_pnl"]) and no_close["open_pnl"] == 0
    assert lib.dhan_rows(pd.DataFrame(), pd.DataFrame()).empty


def test_stored_prev_close(monkeypatch):
    calls = []

    # Stand-in for load_prices: closes for TCS only, and records what was asked.
    def fake_load(symbols, start, end, url=None):
        calls.append((tuple(symbols), end))
        return pd.DataFrame({"TCS": [3400.0, 3450.0]}, index=pd.to_datetime(["2026-10-26", "2026-10-27"]))

    monkeypatch.setattr("marketlens.ingest.prices.load_prices", fake_load)
    rows = pd.DataFrame({"symbol": ["TCS", "NIFTY 25000 CE 03-Nov-2026", "INFY"], "prev_close": [None, None, 1400.0]})
    out = lib.stored_prev_close(rows, dt.date(2026, 10, 28))
    assert out["prev_close"].tolist()[0] == 3450.0 and out["prev_close"].tolist()[2] == 1400.0
    assert calls == [(("TCS",), dt.date(2026, 10, 27))]                  # options are not looked up; never today


# ---------------------------------------------------------------- actions and performance

def test_actions_and_handoffs():
    assert lib.actions_for("options_backtester") == {"backtest": lib.PAGES["options"], "simulator": True,
                                                     "forward": True, "live": True, "why": {}}
    etf = lib.actions_for("etf_momentum")
    assert etf["backtest"] == lib.PAGES["etf"] and not etf["forward"] and "simulator" in etf["why"]
    assert lib.actions_for("forward_indicator")["backtest"] is None
    builder = {"id": 4, "name": "Condor", "kind": "options",
               "params": {"underlying": "NIFTY", "spot_at_save": 25010, "template": "iron_condor"},
               "legs": [{"action": "SELL", "instrument_kind": "CE", "lots": 1, "params": {"strike": 25100}}]}
    key, value, page = lib.handoff_for(builder, "backtest")
    assert key == "handoff" and value["to"] == "backtest" and value["legs"][0]["strike"] == 25100
    with pytest.raises(ValueError, match="Options Backtester first"):
        lib.handoff_for(builder, "forward")
    assert lib.handoff_for({**builder, "params": {"source": "options_backtester"}}, "forward") == ("f_pick", 4, lib.PAGES["forward"])
    key, value, page = lib.handoff_for(builder, "live")
    assert value["to"] == "live" and page == lib.PAGES["live"]
    assert value["web"].endswith(f"/live-trading?strategy={builder['id']}")


def test_performance_curve_and_benchmark():
    trades = pd.DataFrame([
        {"status": "closed", "date": dt.date(2026, 10, 26), "net_pnl": 1000.0, "costs": 20.0, "source": "paper"},
        {"status": "closed", "date": dt.date(2026, 10, 26), "net_pnl": -400.0, "costs": 20.0, "source": "forward"},
        {"status": "closed", "date": dt.date(2026, 10, 28), "net_pnl": 300.0, "costs": 10.0, "source": "paper"},
        {"status": "open", "date": dt.date(2026, 10, 29), "net_pnl": None, "costs": 5.0, "source": "paper"}])
    bench = pd.Series([100.0, 101.0, 102.0], index=pd.to_datetime(["2026-10-26", "2026-10-27", "2026-10-28"]))
    result = lib.performance(trades, 100_000, bench)
    assert result.daily_pnl.tolist() == [600.0, 300.0] and result.equity.iloc[-1] == 100_900
    assert result.metrics["win_rate_pct"] == pytest.approx(200 / 3) and result.metrics["best_day"] == 600
    assert result.benchmark.iloc[-1] == pytest.approx(102_000)
    assert result.extras["costs"] == 50.0
    assert lib.performance(trades[trades["status"] == "open"], 100_000) is None


# ---------------------------------------------------------------- database: strategy library

# Save an options strategy through the backtester's own save function.
def options_strategy(url, name="Straddle 920") -> int:
    settings = template_settings("920_straddle", "NIFTY", dt.date(2026, 1, 1), dt.date(2026, 3, 31))
    return save_backtest_strategy(name, settings, url=url)


def test_library_list_with_backtest_and_forward(db_url, db_session):
    sid = options_strategy(db_url)
    db_session.add_all([
        BacktestRun(strategy_id=sid, period_start=dt.date(2026, 1, 1), period_end=dt.date(2026, 3, 31), status="done",
                    total_pnl=1000, cagr=5, trades_count=10),
        BacktestRun(strategy_id=sid, period_start=dt.date(2026, 1, 1), period_end=dt.date(2026, 6, 30), status="done",
                    total_pnl=2500, cagr=12, max_drawdown=-4, sharpe=1.2, win_rate=60, trades_count=20),
        ForwardTestRun(strategy_id=sid, trade_date=dt.date(2026, 10, 27), status="stopped", capital=500_000, pnl=800),
        ForwardTestRun(strategy_id=sid, trade_date=dt.date(2026, 10, 28), status="stopped", capital=500_000, pnl=-300)])
    db_session.commit()
    row = lib.list_library(db_url).set_index("id").loc[sid]
    assert row["type"] == "Options" and row["made_in"] == "Options Backtester (4)"
    assert row["bt_total_pnl"] == 2500 and row["bt_win_rate_pct"] == 60 and row["bt_runs"] == 2
    assert row["fwd_days"] == 2 and row["fwd_total_pnl"] == 500 and row["fwd_winning_days"] == 1
    assert row["fwd_last_day"] == dt.date(2026, 10, 28) and row["fwd_status"] == "stopped"


def test_duplicate_rename_delete(db_url, db_session):
    sid = options_strategy(db_url)
    copy_id = lib.duplicate_strategy(sid, url=db_url)
    copy = lib.get_strategy(copy_id, db_url)
    assert copy["name"] == "Straddle 920 (copy)" and len(copy["legs"]) == 2
    assert copy["params"] == lib.get_strategy(sid, db_url)["params"]
    assert lib.get_strategy(lib.duplicate_strategy(sid, url=db_url), db_url)["name"] == "Straddle 920 (copy) (2)"
    with pytest.raises(ValueError, match="already called"):
        lib.rename_strategy(copy_id, "Straddle 920", db_url)
    assert lib.rename_strategy(copy_id, "  Straddle B ", db_url) == "Straddle B"
    db_session.add(ForwardTestRun(strategy_id=sid, trade_date=dt.date(2026, 10, 28), status="running", capital=1))
    db_session.commit()
    with pytest.raises(ValueError, match="forward test running"):
        lib.delete_strategy(sid, db_url)
    assert lib.delete_strategy(copy_id, db_url) == "Straddle B"
    assert db_session.scalar(select(func.count()).select_from(StrategyLeg).where(StrategyLeg.strategy_id == copy_id)) == 0


def test_export_import(db_url, db_session):
    sid = options_strategy(db_url)
    text = lib.export_strategies([sid], db_url)
    data = json.loads(text)
    assert data["format"] == lib.EXPORT_FORMAT and data["strategies"][0]["legs"][0]["action"] == "SELL"
    added = lib.import_strategies(text, db_url)                       # same name -> " (2)"
    assert added[0][1] == "Straddle 920 (2)"
    imported = lib.get_strategy(added[0][0], db_url)
    assert imported["params"] == lib.get_strategy(sid, db_url)["params"] and len(imported["legs"]) == 2
    for bad, message in (("{nope", "Not a JSON"), ('{"format": "x"}', "not exported"),
                         (json.dumps({"format": lib.EXPORT_FORMAT, "strategies": []}), "no strategies"),
                         (json.dumps({"format": lib.EXPORT_FORMAT, "strategies": [{"name": "A", "kind": "crypto"}]}),
                          "unknown type")):
        with pytest.raises(ValueError, match=message):
            lib.import_strategies(bad, db_url)
    assert db_session.scalar(select(func.count()).select_from(Strategy)) == 2


def test_link_run(db_url, db_session):
    sid = options_strategy(db_url)
    run = BacktestRun(period_start=dt.date(2026, 1, 1), period_end=dt.date(2026, 2, 1), status="done", total_pnl=5)
    db_session.add(run)
    db_session.commit()
    lib.link_run(run.id, sid, db_url)
    db_session.expire_all()
    assert db_session.get(BacktestRun, run.id).strategy_id == sid
    lib.link_run(None, sid, db_url)                                    # nothing to link: no error


# ---------------------------------------------------------------- database: trades, journal, snapshots

def test_all_trades_journal_and_snapshots(db_url, db_session):
    sid = options_strategy(db_url)
    nifty = Instrument(symbol="NIFTY", instrument_type="INDEX")
    db_session.add(nifty)
    db_session.flush()
    sim = SimulatorSession(underlying_id=nifty.id, trade_date=dt.date(2026, 10, 20), capital=500_000)
    db_session.add(sim)
    db_session.flush()
    db_session.add_all([
        Order(mode="paper", strategy_id=sid, symbol="RELIANCE", side="BUY", product="CNC", qty=10, status="FILLED",
              filled_qty=10, avg_fill_price=1000, costs=3, filled_at=T0),
        Order(mode="paper", strategy_id=sid, symbol="RELIANCE", side="SELL", product="CNC", qty=10, status="FILLED",
              filled_qty=10, avg_fill_price=1050, costs=4, filled_at=T0 + dt.timedelta(days=1)),
        Order(mode="paper", symbol="TCS", side="BUY", qty=1, status="REJECTED", filled_qty=0),
        SimulatorTrade(session_id=sim.id, symbol="NIFTY 25000 CE", side="SELL", qty=65, price=120,
                       market_time=T0 - dt.timedelta(days=6), costs=20, reason="OPEN"),
        SimulatorTrade(session_id=sim.id, symbol="NIFTY 25000 CE", side="BUY", qty=65, price=100,
                       market_time=T0 - dt.timedelta(days=6, hours=-1), costs=20, pnl=1260, reason="MANUAL")])
    db_session.commit()
    trades = lib.all_trades(db_url)
    assert len(trades) == 2 and set(trades["source"]) == {"paper", "simulator"}
    paper = trades[trades["source"] == "paper"].iloc[0]
    assert paper["net_pnl"] == 493.0 and paper["strategy"] == "Straddle 920"

    jid = lib.save_journal(paper.to_dict(), "Followed the plan", lib.make_tags(["Breakout"], [], ["Calm"]), 4,
                           url=db_url)
    assert lib.save_journal(paper.to_dict(), "Edited", ["setup:Pullback"], 3, url=db_url) == jid   # same trade: updated
    with pytest.raises(ValueError, match="1 to 5"):
        lib.save_journal(paper.to_dict(), rating=9, url=db_url)
    table = lib.journal_table(trades, lib.load_journal(db_url)).set_index("key")
    assert table.loc[paper["key"], "notes"] == "Edited" and table.loc[paper["key"], "rating"] == 3
    assert table.loc[paper["key"], "tags"] == ["setup:Pullback"]

    view = lib.holdings_view(pd.DataFrame([{"symbol": "TCS", "kind": "Holding", "qty": 2, "avg_price": 3000.0,
                                            "ltp": 3100.0, "prev_close": 3050.0, "realized_pnl": 0.0,
                                            "opened_today": False, "strategy": "x", "source": "PAPER"}]))
    lib.save_snapshot(view, "paper", dt.date(2026, 10, 27), db_url)
    lib.save_snapshot(view, "paper", dt.date(2026, 10, 28), db_url)
    lib.save_snapshot(view.assign(current_value=6400.0), "paper", dt.date(2026, 10, 28), db_url)     # replaces the day
    history = lib.snapshot_history("paper", db_url)
    assert history["date"].tolist() == [dt.date(2026, 10, 27), dt.date(2026, 10, 28)]
    assert history["value"].tolist() == [6200.0, 6400.0] and history["day_pnl"].tolist() == [100.0, 100.0]
    assert db_session.scalar(select(func.count()).select_from(HoldingsSnapshot)) == 2
    assert lib.snapshot_history("live", db_url).empty
