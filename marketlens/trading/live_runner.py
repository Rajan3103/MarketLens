"""Live runner (module 11): runs every strategy deployed on page 11, one cycle per minute.

    python -m marketlens.trading.live_runner            # all deployed strategies, until Ctrl+C
    python -m marketlens.trading.live_runner --once     # a single cycle (testing)

It reuses the forward-test runner (forward.py), so a deployed strategy follows exactly the same rules as
its backtest and forward test. The difference: every order goes through the order service
(order_service.py), so the risk checks, the kill switch and (live) the Telegram Confirm / Reject apply.
Paper or live follows TRADING_MODE in .env.

- Paper: orders go to the strategy's paper account (the same one its forward test uses).
- Live: orders go to Dhan. The runner keeps a "shadow" copy of what it sent (at the live price), so the
  rules know which legs are open and the page shows the strategy's own P&L. The real positions are Dhan's.
- Confirm mode (live): an order waits for your Confirm. If you confirm, the runner sees it the next minute;
  if the exit comes before you confirmed the entry, the waiting entry is cancelled.
- A heartbeat is written every minute (service_heartbeats, name "live_runner").

Safety
- A strategy deployed for paper never trades live: when TRADING_MODE no longer matches, it is paused.
- Live needs a saved backtest and at least 10 forward-test days (checked at Deploy and again at start).
- Kill switch on: new orders are blocked by the order service, exits still go through. When the positions
  were squared off (by you or SQUARE_OFF_ON_KILL=true), every deployment is stopped.
"""
import argparse
import dataclasses
import datetime as dt
import os
import sys
import time

import pandas as pd

from marketlens import config
from marketlens.broker.base import OrderResult
from marketlens.broker.candles import IST
from marketlens.broker.paper import PaperBroker
from marketlens.logger import get_logger, record_error
from marketlens.trading import forward as fw
from marketlens.trading.order_service import WAITING, OrderService, day_start, save_kill_switch

log = get_logger(__name__)
RUNNER = "live_runner"
MIN_FORWARD_DAYS = 10
ACTIVE = ("running", "paused", "stopping")
SENT_OK = ("PLACED", "PARTIAL", "FILLED")
SHADOW_CAPITAL = 1e12                    # the shadow copy only keeps count; it must never refuse an order


# ====================================================================== deploying (used by page 11)

# How many days a strategy really ran as a forward test (weekends / market-closed days do not count).
def forward_days(strategy_id: int, url: str | None = None) -> int:
    from sqlalchemy import func, select

    from marketlens.db import get_session
    from marketlens.models import ForwardTestRun
    with get_session(url) as session:
        return int(session.scalar(select(func.count(func.distinct(ForwardTestRun.trade_date)))
                                  .where(ForwardTestRun.strategy_id == strategy_id,
                                         ForwardTestRun.trade_date.is_not(None),
                                         ForwardTestRun.state.not_in(("closed", "error")))) or 0)


# True when the strategy has at least one saved backtest.
def has_backtest(strategy_id: int, url: str | None = None) -> bool:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import BacktestRun
    with get_session(url) as session:
        return session.scalar(select(BacktestRun.id).where(BacktestRun.strategy_id == strategy_id).limit(1)) is not None


# What a strategy still needs before it may trade live: {"ok_live", "has_backtest", "forward_days", "missing"}.
def deploy_check(strategy_id: int, url: str | None = None) -> dict:
    backtest, days = has_backtest(strategy_id, url), forward_days(strategy_id, url)
    missing = []
    if not backtest:
        missing.append("a saved backtest (run it on page 4 or 7 and press Save)")
    if days < MIN_FORWARD_DAYS:
        missing.append(f"{MIN_FORWARD_DAYS - days} more forward-test day(s) on page 6 "
                       f"({days} of {MIN_FORWARD_DAYS} done)")
    return {"ok_live": not missing, "has_backtest": backtest, "forward_days": days, "missing": missing}


# Deploy (or re-deploy) a strategy with its settings; it starts at the runner's next minute.
def deploy(strategy_id: int, lots_multiplier: int = 1, max_loss_rs: float | None = None, confirm: bool = True,
           capital: float | None = None, url: str | None = None) -> str:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import LiveDeployment, Strategy
    mode = config.TRADING_MODE
    if int(lots_multiplier) < 1:
        raise ValueError("The lots multiplier must be 1 or more")
    if max_loss_rs is not None and max_loss_rs < 0:
        raise ValueError("The max loss must be 0 (no limit) or more")
    kind, name, _ = fw.load_strategy(strategy_id, url)        # raises when the runner cannot run it
    if mode == "live":
        check = deploy_check(strategy_id, url)
        if not check["ok_live"]:
            raise ValueError("Not ready for live trading. Missing: " + "; ".join(check["missing"]))
    running = fw.status(strategy_id, url)
    if running and running["alive"]:
        raise ValueError("Its forward test is running (page 6): stop it first, they share the paper account")
    with get_session(url) as session:
        strategy = session.get(Strategy, strategy_id)
        if max_loss_rs is not None:                     # the order service reads the limit from here
            strategy.params = {**(strategy.params or {}), "max_loss_rs": float(max_loss_rs)}
        row = session.scalar(select(LiveDeployment).where(LiveDeployment.strategy_id == strategy_id))
        if row is None:
            row = LiveDeployment(strategy_id=strategy_id)
            session.add(row)
        row.mode, row.status, row.lots_multiplier, row.confirm = mode, "running", int(lots_multiplier), bool(confirm)
        row.max_loss_rs, row.capital, row.stopped_at = max_loss_rs, capital, None
        row.started_at, row.state = dt.datetime.now(IST), "waiting"
        row.message = "Deployed: the live runner starts it at its next minute."
        session.commit()
    log.info("Deployed '%s' (%s, %s mode): lots x%s, max loss %s, %s", name, kind, mode, lots_multiplier,
             max_loss_rs, "confirm" if confirm else "auto")
    return f"'{name}' deployed in {mode.upper()} mode."


# Pause (no new orders), resume (running) or stop (close this strategy's positions, then stop).
def set_status(strategy_id: int, status: str, url: str | None = None) -> bool:
    from sqlalchemy import update

    from marketlens.db import get_session
    from marketlens.models import LiveDeployment
    if status not in ("running", "paused", "stopping", "stopped"):
        raise ValueError("status must be running, paused, stopping or stopped")
    if status == "running" and config.TRADING_MODE == "live":
        check = deploy_check(strategy_id, url)
        if not check["ok_live"]:
            raise ValueError("Not ready for live trading. Missing: " + "; ".join(check["missing"]))
    with get_session(url) as session:
        done = session.execute(update(LiveDeployment).where(LiveDeployment.strategy_id == strategy_id)
                               .values(status=status, message=f"{status.capitalize()} from page 11.",
                                       stopped_at=dt.datetime.now(IST) if status == "stopped" else None)).rowcount
        session.commit()
    log.info("Deployment of strategy %s set to %s", strategy_id, status)
    return bool(done)


# Set every active deployment to one status (used by the kill switch buttons and the runner).
def set_all(status: str, message: str, url: str | None = None, only: tuple = ACTIVE) -> int:
    from sqlalchemy import update

    from marketlens.db import get_session
    from marketlens.models import LiveDeployment
    with get_session(url) as session:
        count = session.execute(update(LiveDeployment).where(LiveDeployment.status.in_(only))
                                .values(status=status, message=message,
                                        stopped_at=dt.datetime.now(IST) if status == "stopped" else None)).rowcount
        session.commit()
    return count


# Every deployment with its strategy name, for the page and the runner.
def list_deployments(url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import LiveDeployment, Strategy
    columns = ["strategy_id", "name", "mode", "status", "state", "lots_multiplier", "max_loss_rs", "confirm",
               "pnl", "trade_date", "heartbeat_at", "message"]
    with get_session(url) as session:
        rows = session.execute(select(LiveDeployment, Strategy.name)
                               .join(Strategy, Strategy.id == LiveDeployment.strategy_id)
                               .order_by(Strategy.name)).all()
        out = [{"strategy_id": d.strategy_id, "name": name, "mode": d.mode, "status": d.status, "state": d.state,
                "lots_multiplier": d.lots_multiplier,
                "max_loss_rs": float(d.max_loss_rs) if d.max_loss_rs is not None else None, "confirm": d.confirm,
                "pnl": float(d.pnl or 0), "trade_date": d.trade_date, "heartbeat_at": d.heartbeat_at,
                "message": d.message} for d, name in rows]
    return pd.DataFrame(out, columns=columns)


# ====================================================================== heartbeats

# Write "I am alive" for a background process.
def beat(name: str, message: str = "", url: str | None = None, now: dt.datetime | None = None):
    from sqlalchemy.dialects.postgresql import insert

    from marketlens.db import get_session
    from marketlens.models import ServiceHeartbeat
    now = now or dt.datetime.now(IST)
    values = {"name": name, "pid": os.getpid(), "beat_at": now, "message": message[:500]}
    with get_session(url) as session:
        session.execute(insert(ServiceHeartbeat).values(**values)
                        .on_conflict_do_update(index_elements=["name"], set_=values))
        session.commit()


# The last heartbeat of every process: {name: {"pid", "beat_at", "message"}}.
def heartbeats(url: str | None = None) -> dict:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import ServiceHeartbeat
    with get_session(url) as session:
        return {r.name: {"pid": r.pid, "beat_at": r.beat_at, "message": r.message}
                for r in session.scalars(select(ServiceHeartbeat))}


# ====================================================================== the runner of one strategy

# The strategy with every leg's lots (options) or the quantity (indicator) multiplied.
def scaled(kind: str, strategy, multiplier: int):
    if int(multiplier) <= 1:
        return strategy
    if kind == "options":
        legs = [dataclasses.replace(leg, lots=leg.lots * int(multiplier)) for leg in strategy.legs]
        return dataclasses.replace(strategy, legs=legs)
    return dataclasses.replace(strategy, qty=strategy.qty * int(multiplier))


class RunnerService(OrderService):
    """The order service used inside the runner. When the daily loss limit trips the kill switch it does NOT
    square off here (that would only reach this strategy); the runner loop closes everything instead."""

    # Switch the kill switch on without squaring off (handle_kill_switch() does that for all strategies).
    def trip_kill_switch(self, day_pnl: float, mode: str, now: dt.datetime):
        reason = f"daily loss Rs {-day_pnl:,.0f} reached MAX_DAILY_LOSS_RS Rs {config.MAX_DAILY_LOSS_RS:,.0f}"
        self.kill(reason, square_off=False, mode=mode, now=now)


class LiveRunner(fw.ForwardRunner):
    """One deployed strategy. Same rules as the forward test; orders go through the order service."""

    def __init__(self, strategy_id: int, client=None, url: str | None = None, clock=None, data_dir=None,
                 service: OrderService | None = None):
        dep = get_deployment(strategy_id, url)
        if dep is None:
            raise ValueError(f"Strategy {strategy_id} is not deployed")
        super().__init__(strategy_id, dep["capital"], client=client, url=url, clock=clock, data_dir=data_dir)
        self.strategy = scaled(self.kind, self.strategy, dep["lots_multiplier"])
        self.mode, self.confirm = config.TRADING_MODE, bool(dep["confirm"])
        if self.mode == "live":                                   # shadow copy of what was sent to Dhan
            self.broker = PaperBroker(capital=SHADOW_CAPITAL, costs=self.strategy.costs, save=False,
                                      max_open_positions=10**6, strategy_id=strategy_id)
            self._rebuild_shadow()
        self.service = service or RunnerService(url=url, broker=self.broker if self.mode == "paper" else None,
                                                now=self.clock)
        if self.mode == "live":
            base = self.service.pnl
            self.service.pnl = lambda mode, broker: self._with_own_pnl(base(mode, broker))
        self.signal_of: dict = {}
        self._params: dict = {}

    # ---- the deployment row instead of the forward-test row ----

    # The deployment row; a new trading day starts a fresh live view.
    def _day_row(self, day: dt.date):
        from sqlalchemy import select

        from marketlens.db import get_session
        from marketlens.models import LiveDeployment
        with get_session(self.url) as session:
            row = session.scalar(select(LiveDeployment).where(LiveDeployment.strategy_id == self.strategy_id))
            if row is None:
                raise ValueError(f"Strategy {self.strategy_id} is not deployed any more")
            if row.trade_date != day:
                row.trade_date, row.pnl = day, 0
                row.params = {"baseline_net": self.broker.get_funds()["net_pnl"], "done": {}, "signals": {},
                              "waiting": {}, "kind": self.kind, "name": self.name, "pid": os.getpid()}
                session.commit()
            run_id, status, params = row.id, row.status, dict(row.params or {})
        self.signal_of = params.setdefault("signals", {})
        params.setdefault("waiting", {})
        self._params = params
        if self.mode == "live":
            self._sync_confirmations(params)
        return run_id, status, params

    # Save the live view in the deployment row (with the heartbeat).
    def _save_row(self, run_id: int, **values):
        from marketlens.db import get_session
        from marketlens.models import LiveDeployment
        with get_session(self.url) as session:
            row = session.get(LiveDeployment, run_id)
            for key, value in values.items():
                setattr(row, key, value)
            row.heartbeat_at = dt.datetime.now(IST)
            session.commit()

    # Deployment orders are not tied to a forward-test day.
    def _link_run(self, run_id: int):
        pass

    # One minute: paused = only the heartbeat; stopped = end without touching positions; else the rules.
    def cycle(self) -> dict:
        run_id, status, params = self._day_row(self.clock().date())
        if status == "stopped":
            return {"stop": True, "state": "stopped", "message": "Stopped."}
        if status == "paused":
            message = "Paused: no new orders. Open positions stay as they are."
            self._save_row(run_id, state="paused", message=message, params=params, pnl=self._today_pnl(params))
            return {"stop": False, "state": "paused", "message": message}
        return super().cycle()

    # ---- orders through the order service ----

    # Send one order as a signal: risk checks, kill switch, confirmation (live) all apply.
    def _send(self, symbol: str, side: str, qty: int, product: str = "INTRADAY", tag: str | None = None):
        price = self.broker.last.get(symbol)
        r = self.service.submit_signal(self.strategy_id, side, symbol, int(qty), price=price, product=product,
                                       reason=tag or "", source="live", confirm=self.confirm)
        if tag:
            self.signal_of[tag] = r.signal_id
        if r.status == "SENT":
            result = r.orders[0]
            if self.mode == "live" and result.status in SENT_OK:
                self.broker.place_order(symbol, side, int(qty), product=product, tag=tag)   # the shadow copy
            return result
        if r.status == WAITING:
            self._params.setdefault("waiting", {})[tag or str(r.signal_id)] = r.signal_id
            return OrderResult(r.signal_id, WAITING, message=r.message)
        return OrderResult(None, "REJECTED", message=r.message)

    # An exit is only sent when its entry was really sent; a still-waiting entry is cancelled instead.
    def _entry_ok(self, done: dict, entry_key: str) -> bool:
        status = done.get(entry_key)
        if status in SENT_OK:
            return True
        if status == WAITING:
            sid = self.signal_of.get(f"forward:{entry_key}")
            if sid is not None:
                self.service.cancel_waiting(sid, "The exit came before you confirmed the entry: cancelled")
            self._params.get("waiting", {}).pop(f"forward:{entry_key}", None)
            done[entry_key] = "CANCELLED"
        return False

    # Live: orders you confirmed (or that were cancelled) since the last minute.
    def _sync_confirmations(self, params: dict):
        waiting = params.get("waiting", {})
        for tag, sid in list(waiting.items()):
            rows = signal_orders(sid, self.url)
            statuses = {r["status"] for r in rows}
            if statuses & set(SENT_OK):
                for r in rows:
                    if r["status"] in SENT_OK and r["price"]:
                        self.broker.update_price(r["symbol"], r["price"])
                        self.broker.place_order(r["symbol"], r["side"], r["qty"], product=r["product"], tag=tag)
                new = "PLACED"
            elif statuses and not statuses & {WAITING}:
                new = "CANCELLED"
            else:
                continue
            waiting.pop(tag)
            key = tag.split(":", 1)[1] if tag.startswith("forward:") else tag
            if key in params.get("done", {}):
                params["done"][key] = new

    # Live: rebuild the shadow copy from today's live orders of this strategy (after a restart).
    def _rebuild_shadow(self):
        from sqlalchemy import select

        from marketlens.db import get_session
        from marketlens.models import Order
        with get_session(self.url) as session:
            rows = session.scalars(select(Order).where(Order.mode == "live", Order.strategy_id == self.strategy_id,
                                                       Order.status.in_(SENT_OK),
                                                       Order.placed_at >= day_start(self.clock()))
                                   .order_by(Order.id)).all()
            orders = [(r.symbol, r.side, r.qty, r.product, float(r.avg_fill_price or r.price or 0)) for r in rows]
        for symbol, side, qty, product, price in orders:
            if price > 0:
                self.broker.update_price(symbol, price)
                self.broker.place_order(symbol, side, qty, product=product, tag="restart")

    # Live: the order service's daily total (Dhan's numbers) with this strategy's own P&L from the shadow.
    def _with_own_pnl(self, totals: tuple[float, dict]) -> tuple[float, dict]:
        total, by_strategy = totals
        return total, {**by_strategy, self.strategy_id: self._today_pnl(self._params)}


# One deployment's settings, or None.
def get_deployment(strategy_id: int, url: str | None = None) -> dict | None:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import LiveDeployment
    with get_session(url) as session:
        row = session.scalar(select(LiveDeployment).where(LiveDeployment.strategy_id == strategy_id))
        if row is None:
            return None
        return {"strategy_id": row.strategy_id, "mode": row.mode, "status": row.status,
                "lots_multiplier": row.lots_multiplier, "confirm": row.confirm,
                "capital": float(row.capital) if row.capital is not None else None,
                "max_loss_rs": float(row.max_loss_rs) if row.max_loss_rs is not None else None}


# The orders of one signal (to see whether you confirmed it).
def signal_orders(signal_id, url: str | None = None) -> list[dict]:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Order
    if signal_id is None:
        return []
    with get_session(url) as session:
        return [{"status": r.status, "symbol": r.symbol, "side": r.side, "qty": r.qty, "product": r.product,
                 "price": float(r.avg_fill_price or r.price or 0) or None}
                for r in session.scalars(select(Order).where(Order.signal_id == signal_id))]


# ====================================================================== the loop over all deployments

# Today's latest kill-switch event for a mode, or None.
def kill_event(mode: str, now: dt.datetime, url: str | None = None) -> dict | None:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import KillSwitchEvent
    with get_session(url) as session:
        row = session.scalars(select(KillSwitchEvent)
                              .where(KillSwitchEvent.mode == mode, KillSwitchEvent.ts >= day_start(now),
                                     KillSwitchEvent.ts <= now)
                              .order_by(KillSwitchEvent.ts.desc(), KillSwitchEvent.id.desc()).limit(1)).first()
        return None if row is None else {"active": row.active, "reason": row.reason, "squared_off": row.squared_off}


# Kill switch on: close everything when you chose SQUARE_OFF_ON_KILL; after a square-off stop every deployment.
def handle_kill_switch(mode: str, now: dt.datetime, url: str | None = None, service: OrderService | None = None) -> str:
    event = kill_event(mode, now, url)
    if event is None or not event["active"]:
        return ""
    squared = event["squared_off"]
    if not squared and config.SQUARE_OFF_ON_KILL:
        service = service or OrderService(url=url, now=lambda: now)
        try:
            closed = service.square_off_all(mode)
            save_kill_switch(True, f"{event['reason']} (positions closed: {closed})", mode, now, squared_off=True,
                             url=url)
            squared = True
        except Exception as err:
            record_error(__name__, f"Kill switch square-off failed: {err}", {"mode": mode}, url=url)
    if squared:
        n = set_all("stopped", "Stopped: the kill switch closed every position.", url)
        return f"kill switch: positions closed, {n} deployment(s) stopped"
    return "kill switch on: new orders blocked, exits still allowed"


# One minute for every active deployment; returns one line per strategy.
def run_once(runners: dict, url: str | None = None, clock=None, make_runner=None) -> list[str]:
    clock = clock or (lambda: dt.datetime.now(IST))
    now, mode = clock(), config.TRADING_MODE
    make_runner = make_runner or (lambda sid: LiveRunner(sid, url=url, clock=clock))
    lines = []
    kill = handle_kill_switch(mode, now, url)
    if kill:
        lines.append(kill)
    deployments = list_deployments(url)
    active = deployments[deployments["status"].isin(ACTIVE)]
    for dep in active.itertuples():
        sid = dep.strategy_id
        if dep.mode != mode:
            set_status(sid, "paused" if dep.status != "stopping" else "stopped", url)
            _note(sid, f"Deployed for {dep.mode.upper()}, but TRADING_MODE is {mode}: deploy it again for "
                       f"{mode.upper()}.", url)
            runners.pop(sid, None)
            lines.append(f"{dep.name}: mode changed, paused")
            continue
        try:
            if sid not in runners:
                if mode == "live" and dep.status == "running" and not deploy_check(sid, url)["ok_live"]:
                    set_status(sid, "paused", url)
                    _note(sid, "Paused: not ready for live trading (see Deploy).", url)
                    continue
                runners[sid] = make_runner(sid)
            result = runners[sid].cycle()
        except Exception as err:
            record_error(__name__, f"Live runner: strategy {dep.name} failed: {err}", {"strategy_id": int(sid)}, url=url)
            lines.append(f"{dep.name}: error {err}")
            continue
        if result.get("stop"):
            runners.pop(sid, None)
            set_status(sid, "stopped", url)
        lines.append(f"{dep.name}: {result.get('state')} - {result.get('message', result.get('error', ''))}")
    for sid in set(runners) - set(active["strategy_id"]):        # stopped or removed on the page
        runners.pop(sid)
    beat(RUNNER, f"{mode} mode, {len(active)} active deployment(s)", url, now=dt.datetime.now(IST))
    return lines


# Write a message on a deployment row.
def _note(strategy_id: int, message: str, url: str | None = None):
    from sqlalchemy import update

    from marketlens.db import get_session
    from marketlens.models import LiveDeployment
    with get_session(url) as session:
        session.execute(update(LiveDeployment).where(LiveDeployment.strategy_id == strategy_id)
                        .values(message=message))
        session.commit()


# True when the live runner checked in within the last ALIVE_SECONDS; returns (alive, seconds since).
def runner_alive(url: str | None = None) -> tuple[bool, float | None]:
    last = heartbeats(url).get(RUNNER)
    if not last:
        return False, None
    age = (dt.datetime.now(IST) - last["beat_at"]).total_seconds()
    return age < fw.ALIVE_SECONDS and fw.pid_alive(last["pid"]), age


# Start the live runner in the background (log: logs/live_runner.log); returns its process id.
def start_process() -> int:
    return fw.spawn([sys.executable, "-m", "marketlens.trading.live_runner"], "live_runner.log")


# Command line: python -m marketlens.trading.live_runner [--once] [--at TIME]
def main(argv: list[str] | None = None, url: str | None = None, make_runner=None) -> int:
    parser = argparse.ArgumentParser(description="Run every strategy deployed on page 11 (paper or live).")
    parser.add_argument("--once", action="store_true", help="run a single cycle and stop (for testing)")
    parser.add_argument("--at", default=None, help='pretend it is this time, e.g. "2026-11-03 09:25" (testing)')
    args = parser.parse_args(argv)
    clock = None
    if args.at:
        fixed = dt.datetime.fromisoformat(args.at).replace(tzinfo=IST)
        clock = lambda: fixed  # noqa: E731  (one fixed moment)
    last = heartbeats(url).get(RUNNER)
    if not args.once and last and last["pid"] != os.getpid() and fw.pid_alive(last["pid"]) and \
            (dt.datetime.now(IST) - last["beat_at"]).total_seconds() < fw.ALIVE_SECONDS:
        print(f"The live runner is already running (pid {last['pid']}).")
        return 1
    print(f"Live runner started in {config.TRADING_MODE.upper()} mode "
          f"(AUTO_TRADE={config.AUTO_TRADE}). Press Ctrl+C to stop.", flush=True)
    runners: dict = {}
    while True:
        try:
            lines = run_once(runners, url, clock, make_runner)
        except Exception as err:                       # e.g. the database is down: try again next minute
            log.exception("Live runner cycle failed")
            lines = [f"cycle failed: {err}"]
        for line in lines or ["no active deployments"]:
            print(f"{dt.datetime.now(IST):%H:%M:%S} {line}", flush=True)
        if args.once:
            return 0
        try:
            time.sleep(fw._seconds_to_next_minute(dt.datetime.now(IST)))
        except KeyboardInterrupt:
            print("Stopped with Ctrl+C (positions stay open; Stop a strategy on page 11 to close them).")
            return 0


if __name__ == "__main__":
    sys.exit(main())
