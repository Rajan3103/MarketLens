"""Personal Analytics & Strategy Library (module 10): the functions behind the page.

Four parts, one for each tab:
1. Strategy library: list every saved strategy with its last backtest and forward-test results;
   duplicate, rename, delete, export / import as JSON; which pages each one opens in.
2. Holdings & P&L: holdings and positions (Dhan when the keys are set, paper positions otherwise),
   their value and P&L, allocation by sector, and a daily snapshot for the value chart.
3. Trade journal: every trade from paper, forward-test, live orders and the simulator, rebuilt as
   round trips (open -> close), with your notes, tags, rating and screenshot; stats by tag,
   weekday and strategy.
4. Performance: the combined realised P&L curve and its metrics, next to Nifty 50.

The pure functions (round_trips, holdings_view, stats_by, ...) take tables and return tables, so they
are tested without a database; the load / save functions take `url` (tests use TEST_DATABASE_URL).
"""
import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from marketlens.backtest.engine import BacktestResult, rebase
from marketlens.backtest.metrics import compute_metrics, equity_curve
from marketlens.costs import to_paise
from marketlens.logger import get_logger

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent
JOURNAL_DIR = ROOT / "data" / "journal"
KINDS = {"options": "Options", "indicator": "Indicator", "momentum": "Momentum", "etf": "ETF"}
SOURCES = {"options_backtester": "Options Backtester (4)", "strategy_builder": "Strategy builder (2)",
           "forward_indicator": "Forward test quick rule (6)", "signals_builder": "Signals builder (7)",
           "momentum_engine": "Momentum Engine (8)", "etf_momentum": "ETF Momentum (9)"}
PAGES = {"options": "dashboards/options_backtester.py", "signals": "dashboards/signals_builder.py",
         "etf": "dashboards/etf_momentum.py", "momentum": "dashboards/momentum_engine.py", "simulator": "dashboards/option_simulator.py",
         "forward": "dashboards/forward_test.py", "live": "dashboards/live_trading.py"}
EXPORT_FORMAT = "marketlens-strategies"
SCREENSHOT_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MAX_SCREENSHOT_MB = 10
TAG_GROUPS = {
    "setup": ["Breakout", "Pullback", "Trend follow", "Reversal", "Range", "Expiry day", "News", "Gap"],
    "mistake": ["Early exit", "Late exit", "No stop loss", "Moved stop loss", "Over-sized", "Revenge trade",
                "FOMO entry", "Ignored the plan"],
    "emotion": ["Calm", "Confident", "Fear", "Greed", "Impatient", "Frustrated", "Bored"],
}
TRADE_COLUMNS = ["key", "source", "mode", "strategy_id", "strategy", "symbol", "side", "qty", "entry_time",
                 "entry_price", "exit_time", "exit_price", "gross_pnl", "costs", "net_pnl", "status", "date"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# A plain number from a database Decimal (None stays None).
def _f(value):
    return None if value is None else float(value)


# ================================================================ 1. strategy library

# Which page made a strategy (params["source"]; Strategy-builder rows have no source but a saved spot).
def source_of(params: dict) -> str | None:
    params = params or {}
    if params.get("source"):
        return params["source"]
    return "strategy_builder" if "spot_at_save" in params else None


# What can be done with a strategy from this source: the backtester page, simulator, forward test.
def actions_for(source: str | None) -> dict:
    """Returns {"backtest": page or None, "simulator": bool, "forward": bool, "live": True, "why": {...}}."""
    backtest = {"options_backtester": PAGES["options"], "strategy_builder": PAGES["options"],
                "signals_builder": PAGES["signals"], "etf_momentum": PAGES["etf"],
                "momentum_engine": PAGES["momentum"]}.get(source)
    options = source in ("options_backtester", "strategy_builder")
    forward = source in ("options_backtester", "forward_indicator")
    why = {}
    if backtest is None:
        why["backtest"] = ("Quick forward-test rules have no backtester page; rebuild the rule in the Signals "
                           "builder (7)." if source == "forward_indicator"
                           else "This strategy does not say which page made it.")
    if not options:
        why["simulator"] = "The simulator replays option days; it only opens option strategies."
    if not forward:
        why["forward"] = ("Open it in the Options Backtester first and use \"Start forward test\" there, so the "
                          "legs become rules." if source == "strategy_builder"
                          else "Module 6 runs Options Backtester strategies and quick indicator rules for now.")
    return {"backtest": backtest, "simulator": options, "forward": forward, "live": True, "why": why}


# Everything about one saved strategy, including its legs, as plain values.
def get_strategy(strategy_id: int, url: str | None = None) -> dict:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Strategy, StrategyLeg
    with get_session(url) as session:
        row = session.get(Strategy, strategy_id)
        if row is None:
            raise ValueError(f"No strategy with id {strategy_id}")
        legs = session.scalars(select(StrategyLeg).where(StrategyLeg.strategy_id == row.id)
                               .order_by(StrategyLeg.leg_no)).all()
        return {"id": row.id, "name": row.name, "kind": row.kind, "description": row.description,
                "params": dict(row.params or {}), "tags": list(row.tags or []), "is_active": row.is_active,
                "created_at": row.created_at, "updated_at": row.updated_at,
                "legs": [_leg_dict(leg) for leg in legs]}


# One strategy leg as plain values (for export and duplicates).
def _leg_dict(leg) -> dict:
    return {"leg_no": leg.leg_no, "action": leg.action, "instrument_kind": leg.instrument_kind,
            "strike_rule": leg.strike_rule, "expiry_rule": leg.expiry_rule, "lots": leg.lots,
            "stop_loss_pct": _f(leg.stop_loss_pct), "target_pct": _f(leg.target_pct), "params": dict(leg.params or {})}


# Every saved strategy with its type, dates, last backtest and forward-test results (one row each).
def list_library(url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import func, select

    from marketlens.db import get_session
    from marketlens.models import BacktestRun, ForwardTestRun, Strategy
    columns = ["id", "name", "type", "made_in", "source", "created", "updated", "description", "last_backtest",
               "bt_total_pnl", "bt_cagr_pct", "bt_max_dd_pct", "bt_sharpe", "bt_win_rate_pct", "bt_trades",
               "bt_runs", "fwd_days", "fwd_total_pnl", "fwd_winning_days", "fwd_last_day", "fwd_status"]
    with get_session(url) as session:
        strategies = session.scalars(select(Strategy).order_by(Strategy.created_at.desc(), Strategy.id.desc())).all()
        runs = session.scalars(select(BacktestRun).where(BacktestRun.strategy_id.is_not(None),
                                                         BacktestRun.status == "done")
                               .order_by(BacktestRun.started_at, BacktestRun.id)).all()
        forward = session.execute(
            select(ForwardTestRun.strategy_id, func.count(), func.coalesce(func.sum(ForwardTestRun.pnl), 0),
                   func.count().filter(ForwardTestRun.pnl > 0), func.max(ForwardTestRun.trade_date))
            .group_by(ForwardTestRun.strategy_id)).all()
        latest_status = dict(session.execute(
            select(ForwardTestRun.strategy_id, ForwardTestRun.status)
            .order_by(ForwardTestRun.trade_date.asc().nulls_first(), ForwardTestRun.id)).all())
        last_run, run_count = {}, {}
        for run in runs:                                   # oldest first, so the last one wins
            last_run[run.strategy_id] = run
            run_count[run.strategy_id] = run_count.get(run.strategy_id, 0) + 1
        fwd = {sid: (n, total, wins, last) for sid, n, total, wins, last in forward}
        rows = []
        for s in strategies:
            run, (days, total, wins, last) = last_run.get(s.id), fwd.get(s.id, (0, None, 0, None))
            source = source_of(s.params)
            rows.append({"id": s.id, "name": s.name, "type": KINDS.get(s.kind, s.kind),
                         "made_in": SOURCES.get(source, source or "-"), "source": source,
                         "created": s.created_at, "updated": s.updated_at, "description": s.description,
                         "last_backtest": run.started_at if run else None,
                         "bt_total_pnl": _f(run.total_pnl) if run else None,
                         "bt_cagr_pct": _f(run.cagr) if run else None,
                         "bt_max_dd_pct": _f(run.max_drawdown) if run else None,
                         "bt_sharpe": _f(run.sharpe) if run else None,
                         "bt_win_rate_pct": _f(run.win_rate) if run else None,
                         "bt_trades": run.trades_count if run else None, "bt_runs": run_count.get(s.id, 0),
                         "fwd_days": days, "fwd_total_pnl": _f(total) if days else None, "fwd_winning_days": wins,
                         "fwd_last_day": last, "fwd_status": latest_status.get(s.id)})
    return pd.DataFrame(rows, columns=columns)


# A free name: "Straddle" -> "Straddle (2)", "Straddle (3)" ... when it is taken.
def unique_name(session, name: str) -> str:
    from sqlalchemy import select

    from marketlens.models import Strategy
    name = name.strip()
    taken = set(session.scalars(select(Strategy.name)).all())
    if name not in taken:
        return name
    base = re.sub(r" \(\d+\)$", "", name)
    n = 2
    while f"{base} ({n})" in taken:
        n += 1
    return f"{base} ({n})"


# Add a strategy (and its legs) from plain values; returns its id. Used by duplicate and import.
def _create(session, data: dict, name: str) -> int:
    from marketlens.models import Strategy, StrategyLeg
    if data.get("kind") not in KINDS:
        raise ValueError(f"Unknown strategy type {data.get('kind')!r}: must be one of {', '.join(KINDS)}")
    row = Strategy(name=unique_name(session, name), kind=data["kind"], description=data.get("description"),
                   params=dict(data.get("params") or {}), tags=list(data.get("tags") or []),
                   is_active=bool(data.get("is_active", True)))
    session.add(row)
    session.flush()
    for no, leg in enumerate(data.get("legs") or [], start=1):
        session.add(StrategyLeg(
            strategy_id=row.id, leg_no=int(leg.get("leg_no") or no), action=leg["action"],
            instrument_kind=leg["instrument_kind"], strike_rule=leg.get("strike_rule") or "ATM",
            expiry_rule=leg.get("expiry_rule") or "WEEKLY", lots=int(leg.get("lots") or 1),
            stop_loss_pct=leg.get("stop_loss_pct"), target_pct=leg.get("target_pct"), params=leg.get("params") or {}))
    return row.id


# Copy a strategy with its legs under a new name ("<name> (copy)" by default); returns the new id.
def duplicate_strategy(strategy_id: int, new_name: str | None = None, url: str | None = None) -> int:
    from marketlens.db import get_session
    data = get_strategy(strategy_id, url)
    with get_session(url) as session:
        new_id = _create(session, data, (new_name or f"{data['name']} (copy)").strip() or f"{data['name']} (copy)")
        session.commit()
        return new_id


# Give a strategy a new name (names are unique).
def rename_strategy(strategy_id: int, new_name: str, url: str | None = None) -> str:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Strategy
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("The new name is empty")
    with get_session(url) as session:
        row = session.get(Strategy, strategy_id)
        if row is None:
            raise ValueError(f"No strategy with id {strategy_id}")
        other = session.scalar(select(Strategy.id).where(Strategy.name == new_name, Strategy.id != strategy_id))
        if other is not None:
            raise ValueError(f"Another strategy is already called {new_name!r}")
        row.name = new_name
        session.commit()
        return new_name


# Delete a strategy, its legs and forward-test days (backtest runs, orders and notes stay, unlinked).
def delete_strategy(strategy_id: int, url: str | None = None) -> str:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import ForwardTestRun, Strategy
    with get_session(url) as session:
        row = session.get(Strategy, strategy_id)
        if row is None:
            raise ValueError(f"No strategy with id {strategy_id}")
        running = session.scalar(select(ForwardTestRun.id).where(
            ForwardTestRun.strategy_id == strategy_id, ForwardTestRun.status.in_(["running", "stopping"])))
        if running is not None:
            raise ValueError(f"'{row.name}' has a forward test running: stop it on page 6 first")
        name = row.name
        session.delete(row)
        session.commit()
        log.info("Deleted strategy %s (%s)", strategy_id, name)
        return name


# Point a saved backtest run at a strategy (so the library shows it as that strategy's last backtest).
def link_run(run_id: int | None, strategy_id: int, url: str | None = None):
    from marketlens.db import get_session
    from marketlens.models import BacktestRun
    if not run_id:
        return
    with get_session(url) as session:
        run = session.get(BacktestRun, run_id)
        if run is not None:
            run.strategy_id = strategy_id
            session.commit()


# Turn Decimals and dates into JSON-friendly values.
def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


# Strategies (and their legs) as a JSON text to download and import elsewhere.
def export_strategies(strategy_ids: list[int], url: str | None = None) -> str:
    items = []
    for sid in strategy_ids:
        data = get_strategy(sid, url)
        items.append({k: data[k] for k in ("name", "kind", "description", "tags", "is_active", "params", "legs")})
    return json.dumps({"format": EXPORT_FORMAT, "version": 1,
                       "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       "strategies": _plain(items)}, indent=2)


# Add the strategies from an exported JSON text; a taken name gets " (2)". Returns [(id, name)].
def import_strategies(text: str | bytes, url: str | None = None) -> list[tuple[int, str]]:
    from marketlens.db import get_session
    from marketlens.models import Strategy
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as err:
        raise ValueError(f"Not a JSON file: {err}") from err
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise ValueError("This JSON was not exported from the MarketLens Strategy Library")
    items = data.get("strategies")
    if not isinstance(items, list) or not items:
        raise ValueError("The file has no strategies")
    for n, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise ValueError(f"Strategy {n} in the file has no name")
        if item.get("kind") not in KINDS:
            raise ValueError(f"Strategy {item.get('name')!r} has an unknown type {item.get('kind')!r}")
    added = []
    with get_session(url) as session:
        for item in items:
            new_id = _create(session, item, str(item["name"]))
            added.append((new_id, session.get(Strategy, new_id).name))
        session.commit()
    return added


# What another page needs to open a strategy: (session_state key, value, page file).
def handoff_for(strategy: dict, action: str) -> tuple[str, object, str]:
    """action: backtest, simulator, forward or live. Raises ValueError when not possible."""
    source = source_of(strategy["params"])
    can = actions_for(source)
    if action == "backtest":
        if not can["backtest"]:
            raise ValueError(can["why"]["backtest"])
        if source == "strategy_builder":
            params = strategy["params"]
            legs = [{"side": leg["action"], "option_type": leg["instrument_kind"], "lots": leg["lots"],
                     "strike": leg["params"].get("strike")} for leg in strategy["legs"]]
            return "handoff", {"to": "backtest", "name": strategy["name"], "underlying": params.get("underlying", "NIFTY"),
                               "spot": params.get("spot_at_save"), "legs": legs}, can["backtest"]
        to = {"options_backtester": "backtest_saved", "signals_builder": "signals", "etf_momentum": "etf",
              "momentum_engine": "momentum"}[source]
        return "handoff", {"to": to, "strategy_id": strategy["id"], "name": strategy["name"],
                           "params": strategy["params"]}, can["backtest"]
    if action == "simulator":
        if not can["simulator"]:
            raise ValueError(can["why"]["simulator"])
        return "sim_handoff", {"underlying": strategy["params"].get("underlying", "NIFTY"), "day": None,
                               "expiry": None, "name": strategy["name"], "settings": strategy["params"],
                               "trades": pd.DataFrame()}, PAGES["simulator"]
    if action == "forward":
        if not can["forward"]:
            raise ValueError(can["why"]["forward"])
        return "f_pick", strategy["id"], PAGES["forward"]
    if action == "live":
        return "handoff", {"to": "live", "strategy_id": strategy["id"], "name": strategy["name"],
                           "kind": strategy["kind"], "source": source}, PAGES["live"]
    raise ValueError(f"Unknown action {action!r}")


# ================================================================ 3. trades (journal and performance)

# Filled orders (paper and live) with their strategy names, oldest first.
def load_orders(url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Order, Strategy
    columns = ["id", "mode", "strategy_id", "strategy", "forward_test_run_id", "symbol", "product", "side",
               "qty", "price", "costs", "time"]
    with get_session(url) as session:
        rows = session.execute(
            select(Order, Strategy.name).outerjoin(Strategy, Strategy.id == Order.strategy_id)
            .where(Order.filled_qty > 0, Order.avg_fill_price.is_not(None))
            .order_by(Order.filled_at.nulls_last(), Order.id)).all()
        out = [{"id": o.id, "mode": o.mode, "strategy_id": o.strategy_id, "strategy": name,
                "forward_test_run_id": o.forward_test_run_id, "symbol": o.symbol or "?", "product": o.product,
                "side": o.side, "qty": o.filled_qty, "price": _f(o.avg_fill_price), "costs": _f(o.costs) or 0.0,
                "time": o.filled_at or o.placed_at} for o, name in rows]
    return pd.DataFrame(out, columns=columns)


# Rebuild round trips (open -> close) from fills, the same way the paper broker nets them.
def round_trips(orders: pd.DataFrame) -> pd.DataFrame:
    """orders: id, mode, strategy_id, strategy, forward_test_run_id, symbol, product, side, qty, price, costs,
    time. Fills of one (mode, strategy, symbol, product) add to or reduce one position at its average price;
    a trade ends when the position is back to 0 (a fill that flips it starts the next trade).
    Costs are shared out by quantity. Still-open positions are returned with status "open"."""
    trades = []
    if orders.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    orders = orders.sort_values(["time", "id"])
    groups = orders.groupby(["mode", orders["strategy_id"].fillna(-1), "symbol", "product"], sort=False)
    for _, fills in groups:
        qty, avg, trade = 0, 0.0, None
        for f in fills.itertuples():
            sign, left = (1 if f.side == "BUY" else -1), int(f.qty)
            while left:
                if qty == 0:                                     # a new trade starts
                    trade = {"key": f"order:{f.id}", "mode": f.mode, "strategy_id": f.strategy_id,
                             "strategy": f.strategy, "symbol": f.symbol, "side": f.side, "entry_time": f.time,
                             "in_qty": 0, "in_value": 0.0, "out_qty": 0, "out_value": 0.0, "gross": 0.0,
                             "costs": 0.0, "forward": False}
                trade["forward"] |= pd.notna(f.forward_test_run_id)
                if qty == 0 or (qty > 0) == (sign > 0):          # opening or adding
                    avg = (abs(qty) * avg + left * f.price) / (abs(qty) + left)
                    qty += sign * left
                    trade["in_qty"] += left
                    trade["in_value"] += left * f.price
                    trade["costs"] += f.costs * left / f.qty
                    left = 0
                else:                                            # reducing / closing
                    close = min(left, abs(qty))
                    trade["gross"] += close * (f.price - avg) * (1 if qty > 0 else -1)
                    trade["out_qty"] += close
                    trade["out_value"] += close * f.price
                    trade["costs"] += f.costs * close / f.qty
                    qty += sign * close
                    left -= close
                    if qty == 0:
                        trade["exit_time"] = f.time
                        trades.append(_finish(trade, "closed"))
                        trade = None
        if trade is not None:
            trades.append(_finish(trade, "open"))
    return pd.DataFrame(trades, columns=TRADE_COLUMNS)


# A round trip as one row of TRADE_COLUMNS.
def _finish(t: dict, status: str) -> dict:
    closed = status == "closed"
    source = "live" if t["mode"] == "live" else "forward" if t["forward"] else "paper"
    entry_time = pd.Timestamp(t["entry_time"])
    exit_time = pd.Timestamp(t["exit_time"]) if closed else pd.NaT
    return {"key": t["key"], "source": source, "mode": t["mode"], "strategy_id": t["strategy_id"],
            "strategy": "(no strategy)" if pd.isna(t["strategy"]) else t["strategy"], "symbol": t["symbol"], "side": t["side"],
            "qty": t["in_qty"], "entry_time": entry_time, "entry_price": round(t["in_value"] / t["in_qty"], 4),
            "exit_time": exit_time, "exit_price": round(t["out_value"] / t["out_qty"], 4) if t["out_qty"] else None,
            "gross_pnl": to_paise(t["gross"]) if closed else None, "costs": to_paise(t["costs"]),
            "net_pnl": to_paise(t["gross"] - t["costs"]) if closed else None, "status": status,
            "date": (exit_time if closed else entry_time).date()}


# Simulator orders (one row per order) with the replayed day.
def load_simulator_rows(url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import SimulatorSession, SimulatorTrade
    columns = ["id", "session_id", "symbol", "side", "qty", "price", "market_time", "pnl", "costs", "reason"]
    with get_session(url) as session:
        rows = session.scalars(select(SimulatorTrade).join(SimulatorSession)
                               .order_by(SimulatorTrade.session_id, SimulatorTrade.market_time, SimulatorTrade.id)).all()
        out = [{"id": r.id, "session_id": r.session_id, "symbol": r.symbol or "?", "side": r.side, "qty": r.qty,
                "price": _f(r.price), "market_time": r.market_time, "pnl": _f(r.pnl), "costs": _f(r.costs) or 0.0,
                "reason": r.reason} for r in rows]
    return pd.DataFrame(out, columns=columns)


# Pair each simulator OPEN order with the order that closed it (same session, symbol and size).
def simulator_trades(rows: pd.DataFrame) -> pd.DataFrame:
    """The closing order's pnl is already after both orders' costs (replay.py)."""
    trades = []
    for session_id, part in rows.groupby("session_id", sort=False):
        waiting = []
        for r in part.itertuples():
            if r.reason == "OPEN":
                waiting.append(r)
                continue
            match = next((o for o in waiting if o.symbol == r.symbol and o.qty == r.qty and o.side != r.side), None)
            if match is None:
                continue
            waiting.remove(match)
            costs = (match.costs or 0) + (r.costs or 0)
            net = r.pnl if r.pnl is not None else (r.price - match.price) * r.qty * (1 if match.side == "BUY" else -1) - costs
            trades.append(_sim_row(session_id, match, r, to_paise(net + costs), to_paise(costs), to_paise(net)))
        trades += [_sim_row(session_id, o, None, None, to_paise(o.costs or 0), None) for o in waiting]
    return pd.DataFrame(trades, columns=TRADE_COLUMNS)


# One simulator round trip as a TRADE_COLUMNS row.
def _sim_row(session_id, opened, closed, gross, costs, net) -> dict:
    entry = pd.Timestamp(opened.market_time)
    exit_ = pd.Timestamp(closed.market_time) if closed is not None else pd.NaT
    return {"key": f"sim:{opened.id}", "source": "simulator", "mode": "paper", "strategy_id": None,
            "strategy": f"Simulator session {session_id}", "symbol": opened.symbol, "side": opened.side,
            "qty": opened.qty, "entry_time": entry, "entry_price": opened.price, "exit_time": exit_,
            "exit_price": closed.price if closed is not None else None, "gross_pnl": gross, "costs": costs,
            "net_pnl": net, "status": "closed" if closed is not None else "open",
            "date": (exit_ if closed is not None else entry).date()}


# Every trade from paper, forward-test and live orders and from the simulator, newest first.
def all_trades(url: str | None = None) -> pd.DataFrame:
    parts = [round_trips(load_orders(url)), simulator_trades(load_simulator_rows(url))]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    trades = pd.concat(parts, ignore_index=True)
    return trades.sort_values("entry_time", ascending=False, key=lambda s: pd.to_datetime(s, utc=True)).reset_index(drop=True)


# ---------------------------------------------------------------- journal

# Tags from the three groups, stored as "setup:Breakout", "mistake:Late exit", "emotion:Fear".
def make_tags(setup=(), mistake=(), emotion=()) -> list[str]:
    tags = []
    for group, values in (("setup", setup), ("mistake", mistake), ("emotion", emotion)):
        tags += [f"{group}:{v.strip()}" for v in values if v and v.strip()]
    return list(dict.fromkeys(tags))


# Tags split back into their groups: {"setup": [...], "mistake": [...], "emotion": [...], "other": [...]}.
def split_tags(tags) -> dict:
    out = {"setup": [], "mistake": [], "emotion": [], "other": []}
    for tag in tags or []:
        group, _, value = str(tag).partition(":")
        if value and group in TAG_GROUPS:
            out[group].append(value)
        else:
            out["other"].append(str(tag))
    return out


# Save a screenshot under data/journal/ and return its path (only image files, at most 10 MB).
def save_screenshot(trade_key: str, filename: str, content: bytes, folder: Path = JOURNAL_DIR) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in SCREENSHOT_TYPES:
        raise ValueError(f"The screenshot must be an image ({', '.join(SCREENSHOT_TYPES)})")
    if len(content) > MAX_SCREENSHOT_MB * 1024 * 1024:
        raise ValueError(f"The screenshot is bigger than {MAX_SCREENSHOT_MB} MB")
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", trade_key)
    path = folder / f"{safe}_{dt.datetime.now():%Y%m%d_%H%M%S}{ext}"
    path.write_bytes(content)
    return str(path)


# Save (or update) the journal note of one trade; returns the journal row id.
def save_journal(trade: dict, notes: str = "", tags: list | None = None, rating: int | None = None,
                 screenshot_path: str | None = None, url: str | None = None) -> int:
    """trade: one row of all_trades() as a dict. A new screenshot replaces the old one; None keeps it."""
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import TradeJournal
    if rating is not None and not 1 <= int(rating) <= 5:
        raise ValueError("The rating must be 1 to 5")
    key = trade["key"]
    order_id = int(key.split(":")[1]) if key.startswith("order:") else None
    with get_session(url) as session:
        row = session.scalar(select(TradeJournal).where(TradeJournal.trade_key == key))
        if row is None:
            row = TradeJournal(trade_key=key, order_id=order_id)
            session.add(row)
        row.source = trade["source"]
        row.trade_date = pd.Timestamp(trade["date"]).date()
        row.strategy_id = None if pd.isna(trade.get("strategy_id")) else int(trade["strategy_id"])
        row.title = f"{trade['symbol']} {trade['side']} {pd.Timestamp(trade['entry_time']):%d-%b-%Y %H:%M}"
        row.notes = notes or None
        row.tags = list(tags or [])
        row.rating = int(rating) if rating else None
        row.mistakes = ", ".join(split_tags(tags)["mistake"]) or None
        row.pnl = None if trade.get("net_pnl") is None or pd.isna(trade.get("net_pnl")) else trade["net_pnl"]
        if screenshot_path:
            row.screenshot_path = screenshot_path
        session.commit()
        return row.id


# Journal notes keyed by trade.
def load_journal(url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import TradeJournal
    columns = ["key", "notes", "tags", "rating", "screenshot"]
    with get_session(url) as session:
        rows = session.scalars(select(TradeJournal).where(TradeJournal.trade_key.is_not(None))).all()
        out = [{"key": r.trade_key, "notes": r.notes, "tags": list(r.tags or []), "rating": r.rating,
                "screenshot": r.screenshot_path} for r in rows]
    return pd.DataFrame(out, columns=columns)


# Trades with their journal notes, tags, rating and screenshot next to them.
def journal_table(trades: pd.DataFrame, journal: pd.DataFrame) -> pd.DataFrame:
    table = trades.merge(journal, on="key", how="left") if not journal.empty else trades.assign(
        notes=None, tags=None, rating=None, screenshot=None)
    table["tags"] = table["tags"].map(lambda t: t if isinstance(t, list) else [])
    table["rating"] = table["rating"].astype("Int64")
    return table


# Keep the trades that match the filters (empty filter = no filter).
def filter_trades(table: pd.DataFrame, sources=(), strategies=(), start: dt.date | None = None,
                  end: dt.date | None = None, symbol: str = "", tags=(), result: str = "all") -> pd.DataFrame:
    """result: all, win (net P&L above 0), loss (0 or below), open."""
    keep = pd.Series(True, index=table.index)
    if sources:
        keep &= table["source"].isin(sources)
    if strategies:
        keep &= table["strategy"].isin(strategies)
    if start:
        keep &= table["date"] >= start
    if end:
        keep &= table["date"] <= end
    if symbol.strip():
        keep &= table["symbol"].str.contains(symbol.strip(), case=False, regex=False)
    if tags:
        keep &= table["tags"].map(lambda t: bool(set(t) & set(tags)))
    if result == "win":
        keep &= table["net_pnl"].fillna(0) > 0
    elif result == "loss":
        keep &= (table["status"] == "closed") & (table["net_pnl"].fillna(0) <= 0)
    elif result == "open":
        keep &= table["status"] == "open"
    return table[keep]


# Closed-trade stats grouped by tag, weekday or strategy.
def stats_by(table: pd.DataFrame, by: str) -> pd.DataFrame:
    """by: tag (a trade with 2 tags counts in both), weekday (of the closing day) or strategy."""
    closed = table[table["status"] == "closed"].copy()
    columns = [by.capitalize(), "Trades", "Wins", "Win rate %", "Net P&L", "Average P&L", "Best", "Worst"]
    if closed.empty:
        return pd.DataFrame(columns=columns)
    if by == "tag":
        closed = closed.explode("tags").dropna(subset=["tags"])
        group = closed["tags"]
    elif by == "weekday":
        group = closed["date"].map(lambda d: WEEKDAYS[d.weekday()])
    elif by == "strategy":
        group = closed["strategy"]
    else:
        raise ValueError("by must be tag, weekday or strategy")
    if closed.empty:
        return pd.DataFrame(columns=columns)
    pnl = closed["net_pnl"].astype(float)
    grouped = pnl.groupby(group.values)
    out = pd.DataFrame({"Trades": grouped.size(), "Wins": grouped.apply(lambda s: int((s > 0).sum())),
                        "Net P&L": grouped.sum(), "Average P&L": grouped.mean(), "Best": grouped.max(),
                        "Worst": grouped.min()})
    out["Win rate %"] = out["Wins"] / out["Trades"] * 100
    out = out.rename_axis(by.capitalize()).reset_index()
    if by == "weekday":
        out = out.sort_values(by.capitalize(), key=lambda s: s.map(WEEKDAYS.index))
    else:
        out = out.sort_values("Net P&L", ascending=False)
    return out[columns].round(2).reset_index(drop=True)


# ================================================================ 2. holdings & P&L

# Symbol -> sector (Nifty 200 sectors, ETF categories), read once.
def _sector_map() -> dict:
    from marketlens.ingest.prices import load_universe
    sectors = {}
    for name in ("nifty200", "nifty50"):
        try:
            table = load_universe(name)
            sectors.update(dict(zip(table["symbol"], table["sector"])) if "sector" in table else {})
        except (OSError, ValueError):
            pass
    try:
        etfs = load_universe("etfs")
        sectors.update({s: f"ETF - {c}" for s, c in zip(etfs["symbol"], etfs["category"])})
    except (OSError, ValueError):
        pass
    return sectors


SECTORS: dict | None = None
INDEX_NAMES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY", "BANKEX")


# The sector shown in the allocation chart.
def sector_of(symbol: str) -> str:
    global SECTORS
    if SECTORS is None:
        SECTORS = _sector_map()
    symbol = (symbol or "").strip().upper()
    if symbol in SECTORS and SECTORS[symbol]:
        return SECTORS[symbol]
    words = symbol.replace("-", " ").split()
    if words and (words[-1] in ("CE", "PE") or " CE " in f" {symbol} " or " PE " in f" {symbol} "):
        return "Index options" if words[0] in INDEX_NAMES else "Stock options"
    if "FUT" in words:
        return "Index futures" if words[0] in INDEX_NAMES else "Stock futures"
    return "Other"


HOLDING_COLUMNS = ["symbol", "kind", "qty", "avg_price", "ltp", "prev_close", "realized_pnl", "opened_today",
                   "strategy", "source"]


# Open paper positions (and CNC "holdings") from the positions table.
def paper_rows(url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.broker.candles import IST
    from marketlens.db import get_session
    from marketlens.models import Position, Strategy
    today = dt.datetime.now(IST).date()
    with get_session(url) as session:
        rows = session.execute(select(Position, Strategy.name).outerjoin(Strategy, Strategy.id == Position.strategy_id)
                               .where(Position.is_open, Position.mode == "paper")).all()
        out = [{"symbol": p.symbol or "?", "kind": "Holding" if p.product == "CNC" else "Position", "qty": p.qty,
                "avg_price": _f(p.avg_price), "ltp": _f(p.ltp), "prev_close": None,
                "realized_pnl": _f(p.realized_pnl) - _f(p.costs or 0),
                "opened_today": p.opened_at is not None and p.opened_at.astimezone(IST).date() == today,
                "strategy": name or "(no strategy)", "source": "PAPER"} for p, name in rows]
    return pd.DataFrame(out, columns=HOLDING_COLUMNS)


# The first column of a Dhan table that exists (Dhan's field names differ between endpoints).
def _pick(frame: pd.DataFrame, names: list[str], default=None) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series(default, index=frame.index, dtype=object)


# Dhan holdings (demat) and today's positions as HOLDING_COLUMNS rows.
def dhan_rows(holdings: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    parts = []
    if holdings is not None and not holdings.empty:
        parts.append(pd.DataFrame({
            "symbol": _pick(holdings, ["tradingSymbol", "symbol"]),
            "kind": "Holding", "qty": pd.to_numeric(_pick(holdings, ["totalQty", "qty"]), errors="coerce"),
            "avg_price": pd.to_numeric(_pick(holdings, ["avgCostPrice", "avg_price"]), errors="coerce"),
            "ltp": pd.to_numeric(_pick(holdings, ["lastTradedPrice", "ltp"]), errors="coerce"),
            "prev_close": None, "realized_pnl": 0.0, "opened_today": False, "strategy": "Dhan account",
            "source": "DHAN"}))
    if positions is not None and not positions.empty:
        net = pd.to_numeric(_pick(positions, ["netQty", "qty"], 0), errors="coerce").fillna(0)
        buy_avg = pd.to_numeric(_pick(positions, ["buyAvg"]), errors="coerce")
        sell_avg = pd.to_numeric(_pick(positions, ["sellAvg"]), errors="coerce")
        cost = pd.to_numeric(_pick(positions, ["costPrice", "avg_price"]), errors="coerce")
        avg = cost.where(cost > 0, np.where(net >= 0, buy_avg, sell_avg))
        part = pd.DataFrame({
            "symbol": _pick(positions, ["tradingSymbol", "symbol"]), "kind": "Position", "qty": net,
            "avg_price": avg, "ltp": None, "prev_close": None,
            "realized_pnl": pd.to_numeric(_pick(positions, ["realizedProfit"], 0), errors="coerce").fillna(0),
            "opened_today": _pick(positions, ["productType"], "").astype(str).str.upper() != "CNC",
            "strategy": "Dhan account", "source": "DHAN"})
        parts.append(part[(part["qty"] != 0) | (part["realized_pnl"] != 0)])
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=HOLDING_COLUMNS)
    return pd.concat(parts, ignore_index=True)[HOLDING_COLUMNS]


# Fill in the latest price and yesterday's close from a quote function (symbols -> table).
def add_quotes(rows: pd.DataFrame, quote) -> pd.DataFrame:
    """quote(symbols) returns a table with symbol, ltp and close (= previous close). A symbol it
    cannot price keeps its stored price."""
    rows = rows.copy()
    for i, sym in rows["symbol"].items():
        try:
            q = quote([sym])
        except Exception as err:          # e.g. an option symbol the quote API does not know
            log.info("No quote for %s: %s", sym, err)
            continue
        if q is None or q.empty:
            continue
        q = q.iloc[0]
        if pd.notna(q.get("ltp")):
            rows.at[i, "ltp"] = float(q["ltp"])
        if pd.notna(q.get("close")):
            rows.at[i, "prev_close"] = float(q["close"])
    return rows


# Yesterday's close of shares / ETFs from the stored daily prices (the last close before `day`).
def stored_prev_close(rows: pd.DataFrame, day: dt.date | None = None, url: str | None = None) -> pd.DataFrame:
    from marketlens.ingest.prices import load_prices
    day = day or dt.date.today()
    rows = rows.copy()
    want = [s for s, p in zip(rows["symbol"], rows["prev_close"]) if pd.isna(p) and sector_of(s) not in
            ("Index options", "Stock options", "Index futures", "Stock futures")]
    if not want:
        return rows
    try:
        closes = load_prices(want, day - dt.timedelta(days=10), day - dt.timedelta(days=1), url=url)
    except Exception as err:
        log.info("No stored closes for day P&L: %s", err)
        return rows
    last = closes.ffill().iloc[-1] if not closes.empty else pd.Series(dtype=float)
    rows["prev_close"] = [last.get(s, p) if pd.isna(p) else p for s, p in zip(rows["symbol"], rows["prev_close"])]
    return rows


# Value and P&L of every holding / position (qty below 0 = sold short).
def holdings_view(rows: pd.DataFrame) -> pd.DataFrame:
    """invested = |qty| x average price; current value = |qty| x latest price;
    open P&L = (latest - average) x qty; day P&L = (latest - yesterday's close) x qty, or from the
    average price for a position opened today (blank when yesterday's close is not known);
    total P&L = open P&L + realised P&L after costs."""
    view = rows.copy()
    for col in ("qty", "avg_price", "ltp", "prev_close", "realized_pnl"):
        view[col] = pd.to_numeric(view[col], errors="coerce")
    view["ltp"] = view["ltp"].fillna(view["avg_price"])
    view["sector"] = view["symbol"].map(sector_of)
    view["invested"] = (view["qty"].abs() * view["avg_price"]).round(2)
    view["current_value"] = (view["qty"].abs() * view["ltp"]).round(2)
    view["open_pnl"] = ((view["ltp"] - view["avg_price"]) * view["qty"]).round(2)
    opened_today = view["opened_today"].fillna(False).astype(bool)
    base = view["prev_close"].where(~opened_today, view["avg_price"])
    view["day_pnl"] = ((view["ltp"] - base) * view["qty"]).round(2)
    view["total_pnl"] = (view["open_pnl"] + view["realized_pnl"].fillna(0)).round(2)
    view["return_pct"] = (view["open_pnl"] / view["invested"].replace(0, np.nan) * 100).round(2)
    total = view["current_value"].sum()
    view["weight_pct"] = (view["current_value"] / total * 100).round(2) if total else 0.0
    return view


# Totals for the metric cards.
def holdings_totals(view: pd.DataFrame) -> dict:
    return {"invested": float(view["invested"].sum()), "current_value": float(view["current_value"].sum()),
            "day_pnl": float(view["day_pnl"].sum()), "open_pnl": float(view["open_pnl"].sum()),
            "total_pnl": float(view["total_pnl"].sum()), "count": int(len(view))}


# Current value by sector, with each sector's share in %.
def allocation(view: pd.DataFrame) -> pd.DataFrame:
    if view.empty:
        return pd.DataFrame(columns=["Sector", "Value (Rs)", "Share %"])
    table = view.groupby("sector")["current_value"].sum().sort_values(ascending=False)
    total = table.sum()
    return pd.DataFrame({"Sector": table.index, "Value (Rs)": table.values.round(2),
                         "Share %": (table.values / total * 100).round(2) if total else 0.0})


# Save today's holdings for one account (paper or live); a second save on the same day replaces it.
def save_snapshot(view: pd.DataFrame, mode: str, day: dt.date | None = None, url: str | None = None) -> int:
    from sqlalchemy import delete

    from marketlens.broker.candles import IST
    from marketlens.db import get_session
    from marketlens.models import HoldingsSnapshot
    if mode not in ("paper", "live"):
        raise ValueError("mode must be paper or live")
    day = day or dt.datetime.now(IST).date()
    with get_session(url) as session:
        session.execute(delete(HoldingsSnapshot).where(HoldingsSnapshot.mode == mode,
                                                       HoldingsSnapshot.snapshot_date == day,
                                                       HoldingsSnapshot.strategy_id.is_(None)))
        for r in view.itertuples():
            session.add(HoldingsSnapshot(
                mode=mode, snapshot_date=day, symbol=r.symbol, sector=r.sector, qty=r.qty, price=r.ltp,
                invested=r.invested, value=r.current_value, day_pnl=r.day_pnl,
                weight=None if pd.isna(r.weight_pct) else r.weight_pct))
        session.commit()
    return len(view)


# One row per saved day: invested, value and day P&L of the account.
def snapshot_history(mode: str, url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import func, select

    from marketlens.db import get_session
    from marketlens.models import HoldingsSnapshot
    with get_session(url) as session:
        rows = session.execute(
            select(HoldingsSnapshot.snapshot_date, func.sum(HoldingsSnapshot.invested),
                   func.sum(HoldingsSnapshot.value), func.sum(HoldingsSnapshot.day_pnl))
            .where(HoldingsSnapshot.mode == mode, HoldingsSnapshot.strategy_id.is_(None))
            .group_by(HoldingsSnapshot.snapshot_date).order_by(HoldingsSnapshot.snapshot_date)).all()
    return pd.DataFrame([(d, _f(i), _f(v), _f(p)) for d, i, v, p in rows],
                        columns=["date", "invested", "value", "day_pnl"])


# ================================================================ 4. performance

# Net realised P&L per day (by the day each trade closed).
def daily_realised(trades: pd.DataFrame) -> pd.Series:
    closed = trades[trades["status"] == "closed"]
    if closed.empty:
        return pd.Series(dtype=float, name="pnl")
    daily = closed.groupby("date")["net_pnl"].sum().astype(float).sort_index().round(2)
    daily.index.name, daily.name = "date", "pnl"
    return daily


# The combined realised P&L as a BacktestResult, so the backtest charts (equity, drawdown, monthly
# heatmap) and metrics work on your own trades; benchmark = Nifty 50 closes (optional).
def performance(trades: pd.DataFrame, capital: float, benchmark: pd.Series | None = None,
                name: str = "My realised P&L") -> BacktestResult | None:
    daily = daily_realised(trades)
    if daily.empty:
        return None
    closed = trades[trades["status"] == "closed"]
    start, end = daily.index.min(), daily.index.max()
    metrics = compute_metrics(closed["net_pnl"].astype(float).tolist(), daily, capital, start, end)
    result = BacktestResult(name=name, trades=[], capital=capital, start=start, end=end, daily_pnl=daily,
                            equity=equity_curve(daily, capital), metrics=metrics,
                            benchmark=rebase(benchmark, capital, start, end) if benchmark is not None else None,
                            benchmark_name="Nifty 50 (NIFTYBEES)")
    result.extras["costs"] = float(closed["costs"].sum())
    result.extras["by_source"] = closed.groupby("source")["net_pnl"].agg(["count", "sum"]).rename(
        columns={"count": "Trades", "sum": "Net P&L"}).reset_index()
    return result


# Nifty 50 daily closes (NIFTYBEES with dividends, repaired), or None when not stored.
def nifty_closes(start: dt.date, end: dt.date, url: str | None = None) -> pd.Series | None:
    from marketlens.ingest.clean import repair_scale_errors
    from marketlens.ingest.prices import load_prices
    try:
        closes = load_prices(["NIFTYBEES"], start - dt.timedelta(days=10), end, url=url)
    except Exception as err:
        log.info("No Nifty 50 prices: %s", err)
        return None
    if closes.empty:
        return None
    closes, _, _ = repair_scale_errors(closes)
    return closes["NIFTYBEES"]
