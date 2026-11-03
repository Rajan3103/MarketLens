"""What the Bot Monitor (page 11) shows: login, processes, orders, positions, loss limits and errors.

Every function only reads (the buttons on the page call order_service / live_runner to change things),
so the page stays simple and each piece can be tested on its own.
"""
import datetime as dt
import json
import urllib.request

import pandas as pd

from marketlens import config
from marketlens.broker.candles import IST
from marketlens.logger import get_logger
from marketlens.trading.order_service import WAITING, day_start, kill_switch_status, strategy_pnl_today

log = get_logger(__name__)
ORDER_COLUMNS = ["placed_at", "strategy", "symbol", "side", "qty", "order_type", "status", "broker_order_id",
                 "avg_fill_price", "signal_id", "reason", "message"]
WAITING_COLUMNS = ["signal_id", "placed_at", "strategy", "legs", "reason", "minutes_waiting"]
POSITION_COLUMNS = ["strategy", "symbol", "product", "qty", "avg_price", "ltp", "unrealized_pnl", "realized_pnl"]
ERROR_COLUMNS = ["ts", "level", "module", "message", "details"]


# ---------------------------------------------------------------- status cards

# Today's Dhan login as saved by the last check: {"status", "message", "expiry", "checked_at"} or None.
def login_today(url: str | None = None, now: dt.datetime | None = None) -> dict | None:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import BrokerSession
    now = now or dt.datetime.now(IST)
    with get_session(url) as session:
        row = session.scalar(select(BrokerSession).where(BrokerSession.broker == "dhan",
                                                         BrokerSession.session_date == now.astimezone(IST).date()))
        if row is None:
            return None
        return {"status": row.status, "message": row.message, "expiry": row.token_expiry, "checked_at": row.logged_in_at}


# Is the API server (which also receives the webhook alerts) answering on this computer? (ok, text)
def webhook_status(port: int | None = None, timeout: float = 1.0) -> tuple[bool, str]:
    port = port or config.API_PORT
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as answer:
            ok = json.load(answer).get("status") == "ok"
        return ok, f"Answering on port {port}" if ok else f"Port {port} answers, but not as MarketLens"
    except Exception:
        return False, f"Not running (start: python -m marketlens.api, port {port})"


# A heartbeat as (state, text): state is "alive", "stale" or "never".
def heartbeat_state(beat: dict | None, alive_seconds: float = 150, now: dt.datetime | None = None) -> tuple[str, str]:
    if not beat:
        return "never", "Never started"
    age = ((now or dt.datetime.now(IST)) - beat["beat_at"]).total_seconds()
    if age < alive_seconds:
        return "alive", f"Checked in {age:,.0f} s ago"
    return "stale", f"Last check-in {beat['beat_at'].astimezone(IST):%d-%b %H:%M} ({age / 60:,.0f} min ago)"


# ---------------------------------------------------------------- tables

# Strategy names by id.
def _names(session) -> dict:
    from sqlalchemy import select

    from marketlens.models import Strategy
    return {i: n for i, n in session.execute(select(Strategy.id, Strategy.name))}


# Today's orders (newest first) for one mode, with the signal's reason.
def orders_today(mode: str | None = None, url: str | None = None, now: dt.datetime | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Order, Signal
    mode, now = mode or config.TRADING_MODE, now or dt.datetime.now(IST)
    with get_session(url) as session:
        names = _names(session)
        rows = session.execute(select(Order, Signal.reason).outerjoin(Signal, Signal.id == Order.signal_id)
                               .where(Order.mode == mode, Order.placed_at >= day_start(now))
                               .order_by(Order.id.desc())).all()
        out = [{"placed_at": o.placed_at.astimezone(IST).strftime("%H:%M:%S"),
                "strategy": names.get(o.strategy_id, "manual"), "symbol": o.symbol, "side": o.side, "qty": o.qty,
                "order_type": o.order_type, "status": o.status, "broker_order_id": o.broker_order_id,
                "avg_fill_price": float(o.avg_fill_price) if o.avg_fill_price is not None else None,
                "signal_id": o.signal_id, "reason": reason or o.tag, "message": o.error_message} for o, reason in rows]
    return pd.DataFrame(out, columns=ORDER_COLUMNS)


# Signals whose orders wait for your Confirm, one row per signal (oldest first).
def waiting_signals(url: str | None = None, now: dt.datetime | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Order, Signal
    now = now or dt.datetime.now(IST)
    with get_session(url) as session:
        names = _names(session)
        rows = session.execute(select(Order, Signal.reason).outerjoin(Signal, Signal.id == Order.signal_id)
                               .where(Order.status == WAITING).order_by(Order.id)).all()
        by_signal: dict = {}
        for o, reason in rows:
            item = by_signal.setdefault(o.signal_id, {"signal_id": o.signal_id, "placed_at": o.placed_at,
                                                      "strategy": names.get(o.strategy_id, "manual"), "legs": [],
                                                      "reason": reason})
            item["legs"].append(f"{o.side} {o.qty} {o.symbol}")
    out = [{**v, "legs": "; ".join(v["legs"]), "minutes_waiting": round((now - v["placed_at"]).total_seconds() / 60, 1),
            "placed_at": v["placed_at"].astimezone(IST).strftime("%H:%M:%S")} for v in by_signal.values()]
    return pd.DataFrame(out, columns=WAITING_COLUMNS)


# Open positions: paper from the database; live from Dhan. Returns (table, note).
def open_positions(mode: str | None = None, url: str | None = None, broker=None) -> tuple[pd.DataFrame, str]:
    mode = mode or config.TRADING_MODE
    if mode == "live":
        try:
            from marketlens.broker.dhan_broker import DhanBroker
            table = (broker or DhanBroker(save=False)).get_positions()
            table = table[pd.to_numeric(table["qty"], errors="coerce").fillna(0) != 0]
            return table.assign(strategy="(Dhan account)").reindex(columns=POSITION_COLUMNS), "From your Dhan account."
        except Exception as err:
            return pd.DataFrame(columns=POSITION_COLUMNS), f"Could not read Dhan positions: {err}"
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Position
    with get_session(url) as session:
        names = _names(session)
        rows = session.scalars(select(Position).where(Position.mode == "paper", Position.is_open)
                               .order_by(Position.strategy_id, Position.symbol)).all()
        out = [{"strategy": names.get(p.strategy_id, "manual"), "symbol": p.symbol, "product": p.product, "qty": p.qty,
                "avg_price": float(p.avg_price), "ltp": float(p.ltp) if p.ltp is not None else None,
                "unrealized_pnl": float(p.unrealized_pnl or 0), "realized_pnl": float(p.realized_pnl or 0)}
               for p in rows]
    return pd.DataFrame(out, columns=POSITION_COLUMNS), "Paper positions (virtual money), last saved prices."


# ---------------------------------------------------------------- loss limits

# How much of a loss limit is used: 0.0 (no loss) .. 1.0 (limit reached); a limit of 0 = no limit.
def used_fraction(pnl: float, limit: float | None) -> float:
    if not limit:
        return 0.0
    return min(max(-float(pnl), 0.0) / float(limit), 1.0)


# Live: today's total P&L from Dhan's positions (realised + unrealised), or None when Dhan cannot be read.
def live_day_total(broker=None) -> float | None:
    try:
        from marketlens.broker.dhan_broker import DhanBroker
        table = (broker or DhanBroker(save=False)).get_positions()
    except Exception as err:
        log.warning("Could not read Dhan positions for the daily P&L: %s", err)
        return None
    if table.empty:
        return 0.0
    return float(pd.to_numeric(table["realized_pnl"], errors="coerce").fillna(0).sum()
                 + pd.to_numeric(table["unrealized_pnl"], errors="coerce").fillna(0).sum())


# Today's daily and per-strategy loss usage: ({"pnl", "limit", "used"}, table per strategy).
def loss_limits(mode: str | None = None, url: str | None = None, now: dt.datetime | None = None,
                live_total: float | None = None) -> tuple[dict, pd.DataFrame]:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import LiveDeployment, Strategy
    from marketlens.trading.order_service import strategy_loss_limit
    mode, now = mode or config.TRADING_MODE, now or dt.datetime.now(IST)
    by_strategy = strategy_pnl_today(mode, now, url)
    with get_session(url) as session:
        strategies = {s.id: (s.name, dict(s.params or {})) for s in session.scalars(select(Strategy))}
        for d in session.scalars(select(LiveDeployment).where(LiveDeployment.mode == mode)):
            if d.trade_date == now.astimezone(IST).date() and (mode == "live" or d.strategy_id not in by_strategy):
                by_strategy[d.strategy_id] = float(d.pnl or 0)     # live: the runner's own count
    total = float(live_total) if live_total is not None else sum(by_strategy.values())
    daily = {"pnl": round(total, 2), "limit": float(config.MAX_DAILY_LOSS_RS),
             "used": used_fraction(total, config.MAX_DAILY_LOSS_RS)}
    rows = []
    for sid, pnl in by_strategy.items():
        if sid is None:
            continue
        name, params = strategies.get(sid, (f"strategy {sid}", {}))
        limit = strategy_loss_limit(params)
        rows.append({"strategy_id": sid, "strategy": name, "pnl": round(pnl, 2), "limit": limit,
                     "used": used_fraction(pnl, limit)})
    table = pd.DataFrame(rows, columns=["strategy_id", "strategy", "pnl", "limit", "used"])
    return daily, table.sort_values("used", ascending=False).reset_index(drop=True)


# The kill switch now: (on?, reason).
def kill_state(mode: str | None = None, url: str | None = None, now: dt.datetime | None = None) -> tuple[bool, str]:
    return kill_switch_status(mode or config.TRADING_MODE, now or dt.datetime.now(IST), url)


# ---------------------------------------------------------------- error log

# Errors and warnings from error_log, newest first, filtered by age, level, module and text.
def error_log(days: int = 1, levels: list[str] | None = None, module: str | None = None, text: str = "",
              url: str | None = None, now: dt.datetime | None = None, limit: int = 500) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import ErrorLog
    now = now or dt.datetime.now(IST)
    query = select(ErrorLog).where(ErrorLog.ts >= now - dt.timedelta(days=days))
    if levels:
        query = query.where(ErrorLog.level.in_(levels))
    if module:
        query = query.where(ErrorLog.module == module)
    if text.strip():
        query = query.where(ErrorLog.message.ilike(f"%{text.strip()}%"))
    with get_session(url) as session:
        rows = session.scalars(query.order_by(ErrorLog.ts.desc(), ErrorLog.id.desc()).limit(limit)).all()
        out = [{"ts": r.ts.astimezone(IST).strftime("%d-%b %H:%M:%S"), "level": r.level, "module": r.module,
                "message": r.message, "details": json.dumps(r.details) if r.details else ""} for r in rows]
    return pd.DataFrame(out, columns=ERROR_COLUMNS)


# The module names that have written to error_log (for the filter).
def error_modules(url: str | None = None) -> list[str]:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import ErrorLog
    with get_session(url) as session:
        return sorted(session.scalars(select(ErrorLog.module).distinct()))
