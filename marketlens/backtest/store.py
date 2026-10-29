"""Save a backtest into PostgreSQL (backtest_runs + backtest_trades) and load it back.

    run_id = save_run(strategy_id, params, result)
    result = load_run(run_id)        # the same trades, metrics and charts as before
"""
import datetime as dt
from decimal import Decimal

import pandas as pd
from sqlalchemy import select

from marketlens.backtest.engine import BacktestResult, Trade

PCT_LIMIT = 999_999      # NUMERIC(10, 4) columns hold up to 999,999.9999


# A number for a NUMERIC column, or None when it is missing or too big to store.
def _fit(value, limit: float | None = None):
    if value is None or (limit is not None and abs(value) > limit):
        return None
    return Decimal(str(round(float(value), 4)))


# Save one backtest and all its trades; returns the new run id.
def save_run(strategy_id: int | None, params: dict, result: BacktestResult, url: str | None = None) -> int:
    """strategy_id: the Strategy Library row this run tested (None for a quick test).
    params: the settings used (entry time, stop loss, ...); stored as JSON."""
    from marketlens.db import get_session
    from marketlens.models import BacktestRun, BacktestTrade
    m = result.metrics
    extra = {"name": result.name, "capital": result.capital, "is_demo": result.is_demo,
             "benchmark_name": result.benchmark_name, "values": m,
             "benchmark": ({d.isoformat(): round(float(v), 2) for d, v in result.benchmark.items()}
                           if result.benchmark is not None else None)}
    with get_session(url) as session:
        run = BacktestRun(
            strategy_id=strategy_id, params={**result.params, **(params or {})},
            period_start=result.start, period_end=result.end, status="done",
            total_pnl=_fit(m["total_pnl"]), cagr=_fit(m["cagr_pct"], PCT_LIMIT),
            max_drawdown=_fit(m["max_drawdown_pct"], PCT_LIMIT), sharpe=_fit(m["sharpe"], PCT_LIMIT),
            win_rate=_fit(m["win_rate_pct"], PCT_LIMIT), trades_count=m["trades"], metrics=extra,
            finished_at=dt.datetime.now(dt.timezone.utc))
        session.add(run)
        session.flush()
        session.add_all([BacktestTrade(
            run_id=run.id, leg_no=t.leg_no, symbol=t.symbol, side=t.side, qty=t.qty,
            entry_time=t.entry_time, entry_price=_fit(t.entry_price), exit_time=t.exit_time,
            exit_price=_fit(t.exit_price), pnl=_fit(t.pnl), costs=_fit(t.costs),
            exit_reason=t.exit_reason, note=t.note) for t in result.trades])
        session.commit()
        return run.id


# Load a saved backtest back into a BacktestResult (metrics are worked out again from the trades).
def load_run(run_id: int, url: str | None = None) -> BacktestResult:
    from marketlens.db import get_session
    from marketlens.models import BacktestRun, BacktestTrade
    with get_session(url) as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise ValueError(f"No backtest run with id {run_id}")
        rows = session.scalars(select(BacktestTrade).where(BacktestTrade.run_id == run_id)
                               .order_by(BacktestTrade.exit_time, BacktestTrade.leg_no)).all()
        trades = [Trade(r.symbol or "", r.side, r.qty, r.entry_time, float(r.entry_price),
                        r.exit_time, float(r.exit_price), leg_no=r.leg_no, exit_reason=r.exit_reason, note=r.note,
                        costs=float(r.costs or 0)) for r in rows]
        extra, params = run.metrics or {}, run.params or {}
        start, end = run.period_start, run.period_end
    bench = extra.get("benchmark")
    benchmark = pd.Series({dt.date.fromisoformat(d): v for d, v in bench.items()}) if bench else None
    return BacktestResult.build(trades, extra.get("capital", 100_000), start, end,
                                name=extra.get("name", f"Run {run_id}"), benchmark=benchmark,
                                benchmark_name=extra.get("benchmark_name", ""), params=params,
                                is_demo=extra.get("is_demo", False))


# A short list of saved runs (newest first) for a "load a past run" select box.
def list_runs(limit: int = 50, url: str | None = None) -> pd.DataFrame:
    from marketlens.db import get_session
    from marketlens.models import BacktestRun
    with get_session(url) as session:
        runs = session.scalars(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)).all()
        return pd.DataFrame([{"id": r.id, "name": (r.metrics or {}).get("name", ""),
                              "start": r.period_start, "end": r.period_end,
                              "total_pnl": float(r.total_pnl) if r.total_pnl is not None else None,
                              "trades": r.trades_count, "saved": r.finished_at} for r in runs])
