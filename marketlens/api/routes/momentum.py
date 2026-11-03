"""Momentum Engine Backtest vs Nifty (module 8).

    GET  /api/momentum/options                   default settings and the choices for the form
    POST /api/momentum/data-check                which stored prices are missing for these settings
    POST /api/momentum/backtest                  run a backtest (background job; the result has a run_id)
    POST /api/momentum/sync-prices               download missing prices with sync_prices() (background job)
    GET  /api/momentum/ranks?universe=nifty200   today's ranks (historical model output, not advice)
    GET  /api/momentum/runs                      saved momentum runs, newest first
    GET  /api/momentum/runs/{run_id}/rebalances  buys and sells on each rebalance of a saved run
    POST /api/momentum/strategies                save the settings in the Strategy Library
    GET  /api/momentum/strategies/{id}           settings of a saved strategy (the library opens it here)

The engine is marketlens/momentum/portfolio_bt.py. Historical analysis for learning, not a live trading
recommendation.
"""
import datetime as dt
import json
from functools import partial

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from marketlens.api.errors import ApiError
from marketlens.api.jobs import start_job
from marketlens.api.results import result_json
from marketlens.api.schemas import df_to_records, to_json_value
from marketlens.logger import get_logger
from marketlens.momentum import portfolio_bt as pb
from marketlens.momentum.ranking import METHODS

log = get_logger(__name__)
router = APIRouter(prefix="/api/momentum", tags=["momentum"])
NOTE = "Historical analysis for learning, not a live trading recommendation."
RANKS_NOTE = ("Historical model output for learning, not advice: these ranks come from past prices only and "
              "say nothing certain about the future.")


class SettingsBody(BaseModel):
    """StockMomentumSettings as plain values (see GET /options for the defaults); missing ones keep defaults."""
    settings: dict = Field(default_factory=dict)


class BacktestBody(SettingsBody):
    allow_missing: bool = False          # run without the symbols whose prices are missing
    save: bool = True                    # save the run and its holdings (needed for Rebalance history)
    strategy_id: int | None = None       # the Strategy Library row this run tested


class StrategyBody(SettingsBody):
    name: str = Field(min_length=1, max_length=100)
    notes: str = ""


# The database for this app (tests pass their own).
def _url(request: Request) -> str | None:
    return getattr(request.app.state, "url", None)


# Settings from the request, checked; a bad one is a friendly 400.
def _settings(values: dict) -> pb.StockMomentumSettings:
    settings = pb.StockMomentumSettings.from_params(values)
    settings.check()
    return settings


# Default settings and every choice the form needs.
@router.get("/options")
def options() -> dict:
    sizes = {u: len(pb.universe_table(u)) for u in pb.UNIVERSES}
    return {"defaults": pb.StockMomentumSettings().to_params(),
            "universes": [{"id": "nifty50", "label": f"Nifty 50 ({sizes['nifty50']} stocks)"},
                          {"id": "nifty200", "label": f"Nifty 200 ({sizes['nifty200']} stocks)"}],
            "rebalance": list(pb.REBALANCE), "weightings": list(pb.WEIGHTINGS), "methods": list(METHODS),
            "safe_assets": ["LIQUIDBEES", "CASH"], "note": NOTE}


# Which prices are missing: [{symbol, problem, ...}] and a sentence for the page.
@router.post("/data-check")
def data_check(body: SettingsBody, request: Request) -> dict:
    settings = _settings(body.settings)
    problems = pb.price_problems(settings, _url(request))
    message = (f"All prices for {settings.universe} are stored." if not problems
               else str(pb.MissingPricesError(problems)))
    return {"ok": not problems, "problems": df_to_records(_frame(problems)), "message": message,
            "warning": pb.history_warning(settings, _url(request)), "needed_from": settings.history_start().isoformat()}


# A list of dicts as a table (empty list -> empty table).
def _frame(rows: list[dict]):
    import pandas as pd
    return pd.DataFrame(rows)


# The backtest job: run, save (run + holdings), and return everything the results component shows.
def backtest_work(job, settings: dict, allow_missing: bool, save: bool, strategy_id: int | None, url: str | None):
    s = pb.StockMomentumSettings.from_params(settings)
    result = pb.run_momentum_backtest(s, progress=job.progress, allow_missing=allow_missing, url=url)
    if save:
        job.progress(99.5, "Saving the run and its holdings")
        result.extras["run_id"] = pb.save_momentum_run(result, strategy_id, url=url)
    return result_json(result)


# Start a backtest in the background; follow it at GET /api/jobs/{job_id} (its result is the full report).
@router.post("/backtest")
def backtest(body: BacktestBody, request: Request) -> dict:
    settings = _settings(body.settings)
    url = _url(request)
    # the database address goes in with partial(), never into the saved job settings (it holds a password)
    job_id = start_job("momentum_backtest", partial(backtest_work, url=url),
                       {"settings": settings.to_params(), "allow_missing": body.allow_missing, "save": body.save,
                        "strategy_id": body.strategy_id}, url=url)
    return {"job_id": job_id}


# The download job: stocks, Nifty 50 and the Momentum 30 ETFs, from the first day the settings need.
def sync_work(job, settings: dict, url: str | None):
    return pb.sync_universe_prices(pb.StockMomentumSettings.from_params(settings), progress=job.progress, url=url)


# Download missing prices in the background (yfinance -> daily_prices).
@router.post("/sync-prices")
def sync_prices(body: SettingsBody, request: Request) -> dict:
    settings = _settings(body.settings)
    url = _url(request)
    return {"job_id": start_job("momentum_sync_prices", partial(sync_work, url=url),
                                {"settings": settings.to_params()}, url=url)}


# Today's ranking from the last stored close: the form's settings (JSON), a saved strategy's, or the defaults.
@router.get("/ranks")
def ranks(request: Request, universe: str = Query("nifty200"), top_n: int = Query(20, ge=1, le=50),
          strategy_id: int | None = Query(None), date: dt.date | None = Query(None),
          settings: str | None = Query(None, description="the form's settings as JSON text")) -> dict:
    url = _url(request)
    if settings:
        try:
            values = json.loads(settings)
        except ValueError:
            raise ApiError("settings must be JSON text") from None
        settings = _settings(values if isinstance(values, dict) else {})
    elif strategy_id is not None:
        try:
            settings = pb.load_momentum_strategy(strategy_id, url)
        except LookupError as err:
            raise ApiError(str(err), status=404) from None
    else:
        settings = _settings({"universe": universe, "top_n": top_n,
                              "hold_rank": max(top_n, pb.StockMomentumSettings().hold_rank)})
    table = pb.current_ranks(settings, date, url)
    a = table.attrs
    return {"universe": settings.universe, "top_n": settings.top_n, "as_of": to_json_value(a.get("as_of")),
            "market_ok": a.get("market_ok"), "index_close": to_json_value(a.get("index_close")),
            "index_average": to_json_value(a.get("index_average")), "hold": a.get("hold"),
            "index_filter": settings.filters.index_filter, "rows": df_to_records(table), "note": RANKS_NOTE}


# Saved momentum runs, newest first (for the Rebalance history tab).
@router.get("/runs")
def runs(request: Request, limit: int = Query(30, ge=1, le=200)) -> dict:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import BacktestRun
    with get_session(_url(request)) as session:
        rows = session.scalars(select(BacktestRun).where(BacktestRun.params["source"].astext == "momentum_engine")
                               .order_by(BacktestRun.id.desc()).limit(limit)).all()
        out = [{"run_id": r.id, "name": (r.metrics or {}).get("name"), "start": r.period_start.isoformat(),
                "end": r.period_end.isoformat(), "cagr_pct": to_json_value(r.cagr),
                "max_drawdown_pct": to_json_value(r.max_drawdown), "strategy_id": r.strategy_id,
                "saved": to_json_value(r.finished_at)} for r in rows]
    return {"runs": out}


# Buys and sells on each rebalance of a saved run.
@router.get("/runs/{run_id}/rebalances")
def rebalances(run_id: int, request: Request) -> dict:
    try:
        history = pb.rebalance_history(run_id, _url(request))
    except LookupError as err:
        raise ApiError(str(err), status=404) from None
    return {"run_id": run_id, "rebalances": [{**h, "date": h["date"].isoformat()} for h in history], "note": NOTE}


# Save the settings as a "momentum" strategy (same name = update).
@router.post("/strategies")
def save_strategy(body: StrategyBody, request: Request) -> dict:
    settings = _settings(body.settings)
    strategy_id = pb.save_momentum_strategy(body.name, settings, body.notes, _url(request))
    return {"strategy_id": strategy_id, "name": body.name.strip(),
            "message": f"Saved '{body.name.strip()}' in the Strategy Library (id {strategy_id})."}


# A saved strategy's settings, so the page can open it (Strategy Library -> Open in backtester).
@router.get("/strategies/{strategy_id}")
def get_strategy(strategy_id: int, request: Request) -> dict:
    from marketlens.db import get_session
    from marketlens.models import Strategy
    try:
        settings = pb.load_momentum_strategy(strategy_id, _url(request))
    except LookupError as err:
        raise ApiError(str(err), status=404) from None
    with get_session(_url(request)) as session:
        row = session.get(Strategy, strategy_id)
        name, notes = row.name, row.description
    return {"strategy_id": strategy_id, "name": name, "notes": notes, "settings": settings.to_params()}
