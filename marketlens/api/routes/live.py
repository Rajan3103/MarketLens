"""Live Algo Trading & Bot Monitor (module 11).

    GET  /api/live/status                         banner (PAPER / LIVE, AUTO_TRADE, kill switch) and status cards
    GET  /api/live/strategies                     runnable strategies from the library, with what each still needs
    GET  /api/live/strategies/{id}/check          saved backtest? forward-test days? (live needs both)
    POST /api/live/deploy                         deploy with lots multiplier, max loss, auto / confirm
    GET  /api/live/deployments                    every deployment with today's P&L
    POST /api/live/deployments/{id}/status        running (start), paused or stopping (stop)
    GET  /api/live/monitor                        running strategies, open positions, today's orders, waiting orders
    POST /api/live/signals/{id}/confirm|reject    act on an order waiting for your Confirm
    GET  /api/live/limits                         daily and per-strategy loss limits
    POST /api/live/kill, /api/live/unkill         kill switch on / off
    POST /api/live/square-off                     close every position, kill switch on, stop every deployment
    POST /api/live/runner/start                   start the live runner in the background
    POST /api/live/login/check                    check today's Dhan login now
    GET  /api/live/errors?days=1&level=ERROR      the error log with filters

The work is done by marketlens/trading/live_runner.py, monitor.py and order_service.py (the same code as
the Streamlit page 11). Paper or live follows TRADING_MODE in .env; the page cannot change it.
"""
import datetime as dt

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from marketlens import config
from marketlens.api.errors import ApiError
from marketlens.api.schemas import df_to_records, to_json_value
from marketlens.broker.candles import IST
from marketlens.trading import forward as fw
from marketlens.trading import live_runner as lr
from marketlens.trading import monitor as mon
from marketlens.trading.order_service import OrderService, market_is_open

router = APIRouter(prefix="/api/live", tags=["live"])
KILL_WORD = "KILL"                     # the page sends these after the tick box and the second click
SQUARE_OFF_WORD = "SQUARE OFF"


# The time now in India (tests replace it to stand on a fixed trading day).
def now() -> dt.datetime:
    return dt.datetime.now(IST)


class DeployBody(BaseModel):
    strategy_id: int
    lots_multiplier: int = Field(1, ge=1, le=50)
    max_loss_rs: float | None = Field(None, ge=0, description="this strategy's loss limit today; 0 = none")
    confirm: bool = True                # live: every order waits for your Confirm on Telegram / this page
    capital: float | None = Field(None, ge=10_000, description="paper only")


class StatusBody(BaseModel):
    status: str = Field(description="running (start), paused or stopping (stop)")


class ConfirmBody(BaseModel):
    confirm: str = Field("", description=f"'{KILL_WORD}' or '{SQUARE_OFF_WORD}', so a stray click does nothing")
    reason: str = ""


# The database for this app (tests pass their own).
def _url(request: Request) -> str | None:
    return getattr(request.app.state, "url", None)


# A datetime as India time text, or None.
def _ist(value) -> str | None:
    return value.astimezone(IST).isoformat() if value else None


# A heartbeat card: {state: alive / stale / never, ok, text}.
def _beat_card(beat: dict | None, never: str = "Never started") -> dict:
    state, text = mon.heartbeat_state(beat)
    return {"state": state, "ok": None if state == "never" else state == "alive",
            "text": never if state == "never" else text, "at": _ist(beat["beat_at"]) if beat else None}


# The banner and the six status cards.
@router.get("/status")
def status(request: Request) -> dict:
    from marketlens.broker.session import DAILY_STEPS
    from marketlens.db import connection_status
    url = _url(request)
    moment = now()
    db_ok, db_text = connection_status(url)
    killed, why = mon.kill_state(url=url, now=moment) if db_ok else (False, "")
    login = mon.login_today(url, moment) if db_ok else None
    beats = lr.heartbeats(url) if db_ok else {}
    login_card = ({"ok": False, "status": "not checked", "text": "Not checked today", "expiry": None}
                  if login is None else
                  {"ok": login["status"] == "active", "status": login["status"], "text": login["message"] or login["status"],
                   "expiry": _ist(login["expiry"]), "checked_at": _ist(login["checked_at"])})
    login_card["renew_steps"] = DAILY_STEPS
    runner = _beat_card(beats.get(lr.RUNNER))
    runner["alive"] = lr.runner_alive(url)[0] if db_ok else False
    return {
        "mode": config.TRADING_MODE.upper(), "live": config.TRADING_MODE == "live", "auto_trade": config.AUTO_TRADE,
        "kill_switch": {"on": killed, "reason": why}, "market_open": market_is_open(moment), "now": moment.isoformat(),
        "cards": {
            "login": login_card,
            "runner": runner,
            "api": {"ok": True, "text": f"Answering (version {_version()})"},     # this answer proves it
            "telegram": _beat_card(beats.get("telegram_bot"), "Not built yet (module 12)") |
            {"configured": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)},
            "scheduler": _beat_card(beats.get("scheduler"), "Not built yet"),
            "database": {"ok": db_ok, "text": "Connected" if db_ok else db_text},
        },
        "confirm_timeout_min": config.CONFIRM_TIMEOUT_MIN, "min_forward_days": lr.MIN_FORWARD_DAYS,
    }


# The app's version (for the API card).
def _version() -> str:
    import marketlens
    return marketlens.__version__


# What one strategy still needs before live trading.
def _check(strategy_id: int, url: str | None) -> dict:
    check = lr.deploy_check(strategy_id, url)
    return {**check, "min_forward_days": lr.MIN_FORWARD_DAYS,
            "can_deploy": check["ok_live"] or config.TRADING_MODE != "live"}


# Strategies the runner can run (options legs or an indicator rule), each with its live check.
@router.get("/strategies")
def strategies(request: Request) -> dict:
    url = _url(request)
    table = fw.list_strategies(url)
    rows = [{**r, "check": _check(int(r["id"]), url)} for r in df_to_records(table)]
    return {"strategies": rows, "mode": config.TRADING_MODE.upper()}


# One strategy's check (saved backtest, forward-test days).
@router.get("/strategies/{strategy_id}/check")
def strategy_check(strategy_id: int, request: Request) -> dict:
    return _check(strategy_id, _url(request))


# Deploy (or re-deploy) a strategy; it starts at the live runner's next minute.
@router.post("/deploy")
def deploy(body: DeployBody, request: Request) -> dict:
    live = config.TRADING_MODE == "live"
    message = lr.deploy(body.strategy_id, body.lots_multiplier, body.max_loss_rs, body.confirm,
                        None if live else body.capital, _url(request))
    return {"message": message, "deployment": lr.get_deployment(body.strategy_id, _url(request))}


# Every deployment (newest settings) with today's P&L.
@router.get("/deployments")
def deployments(request: Request) -> dict:
    table = lr.list_deployments(_url(request))
    return {"deployments": df_to_records(table)}


# Start (running), pause or stop (stopping: positions closed at the runner's next minute) one deployment.
@router.post("/deployments/{strategy_id}/status")
def set_status(strategy_id: int, body: StatusBody, request: Request) -> dict:
    if body.status not in ("running", "paused", "stopping"):
        raise ApiError("status must be running, paused or stopping")
    if not lr.set_status(strategy_id, body.status, _url(request)):
        raise ApiError(f"Strategy {strategy_id} is not deployed", status=404)
    words = {"running": "Started", "paused": "Paused", "stopping": "Stopping: positions close at the next minute"}
    return {"message": words[body.status], "deployment": lr.get_deployment(strategy_id, _url(request))}


# Running strategies, open positions, today's orders and orders waiting for your Confirm.
@router.get("/monitor")
def monitor(request: Request) -> dict:
    url = _url(request)
    table = lr.list_deployments(url)
    running = table[table["status"].isin(lr.ACTIVE)]
    positions, note = mon.open_positions(url=url)
    return {"running": df_to_records(running), "positions": df_to_records(positions), "positions_note": note,
            "orders": df_to_records(mon.orders_today(url=url, now=now())),
            "waiting": df_to_records(mon.waiting_signals(url, now()))}


# The answer of confirm / reject.
def _signal_answer(result) -> dict:
    return {"signal_id": result.signal_id, "status": result.status, "message": result.message}


# Send the orders of a waiting signal.
@router.post("/signals/{signal_id}/confirm")
def confirm(signal_id: int, request: Request) -> dict:
    result = OrderService(url=_url(request)).confirm_signal(signal_id)
    if result.status == "ERROR":
        raise ApiError(result.message, status=404 if "waiting" in result.message else 400)
    return _signal_answer(result)


# Cancel the orders of a waiting signal.
@router.post("/signals/{signal_id}/reject")
def reject(signal_id: int, request: Request) -> dict:
    result = OrderService(url=_url(request)).reject_signal(signal_id)
    if result.status == "ERROR":
        raise ApiError(result.message, status=404)
    return _signal_answer(result)


# Daily and per-strategy loss limits: P&L, limit and how much is used (0..1).
@router.get("/limits")
def limits(request: Request) -> dict:
    live = config.TRADING_MODE == "live"
    daily, per = mon.loss_limits(url=_url(request), now=now(), live_total=mon.live_day_total() if live else None)
    killed, why = mon.kill_state(url=_url(request), now=now())
    return {"daily": daily, "strategies": df_to_records(per), "kill_switch": {"on": killed, "reason": why},
            "square_off_on_kill": config.SQUARE_OFF_ON_KILL}


# Kill switch on: no new orders today (exits still go through). Needs confirm = "KILL".
@router.post("/kill")
def kill(body: ConfirmBody, request: Request) -> dict:
    if body.confirm != KILL_WORD:
        raise ApiError(f"Tick the box and press the button twice (confirm must be '{KILL_WORD}')")
    message = OrderService(url=_url(request)).kill(body.reason or "switched on from the web page (module 11)")
    return {"message": message or "Kill switch ON: no new orders today.", "on": True}


# Kill switch off.
@router.post("/unkill")
def unkill(request: Request) -> dict:
    OrderService(url=_url(request)).unkill("switched off from the web page (module 11)")
    return {"message": "Kill switch OFF: orders allowed again.", "on": False}


# Close every position, switch the kill switch on and stop every deployment. Needs confirm = "SQUARE OFF".
@router.post("/square-off")
def square_off(body: ConfirmBody, request: Request) -> dict:
    if body.confirm != SQUARE_OFF_WORD:
        raise ApiError(f"Tick the box and press the button twice (confirm must be '{SQUARE_OFF_WORD}')")
    url = _url(request)
    stopped = lr.set_all("stopped", "Stopped: square off everything from the web page.", url)
    result = OrderService(url=url).kill("square off everything from the web page (module 11)", square_off=True)
    return {"message": f"Done: {result}", "stopped": stopped}


# Start the live runner in the background (unless it already runs).
@router.post("/runner/start")
def start_runner(request: Request) -> dict:
    alive, _ = lr.runner_alive(_url(request))
    if alive:
        return {"message": "The live runner is already running.", "pid": None}
    pid = lr.start_process()
    return {"message": f"Started in the background (process {pid}). Log: logs/live_runner.log", "pid": pid}


# Check today's Dhan login now (asks Dhan; saves the answer for the status card).
@router.post("/login/check")
def login_check(request: Request) -> dict:
    from marketlens.broker.session import check_login
    result = check_login(url=_url(request))
    return {"status": result.status, "message": result.message, "ok": result.status == "active"}


# The error log: newest first, filtered by period (days), level, module and text.
@router.get("/errors")
def errors(request: Request, days: int = Query(1, ge=1, le=365), level: list[str] = Query(["ERROR", "WARNING"]),
           module: str | None = Query(None), text: str = Query("", max_length=200)) -> dict:
    url = _url(request)
    table = mon.error_log(days, [lv.upper() for lv in level], module or None, text, url)
    return {"errors": df_to_records(table), "modules": mon.error_modules(url), "count": len(table)}


# Settings of the app that matter on this page (for the help texts).
@router.get("/settings")
def live_settings() -> dict:
    return {key: to_json_value(getattr(config, key)) for key in (
        "TRADING_MODE", "AUTO_TRADE", "MAX_DAILY_LOSS_RS", "MAX_STRATEGY_LOSS_RS", "MAX_OPEN_POSITIONS",
        "MAX_CAPITAL_PER_TRADE_RS", "PAPER_CAPITAL", "SQUARE_OFF_ON_KILL", "CONFIRM_TIMEOUT_MIN")}
