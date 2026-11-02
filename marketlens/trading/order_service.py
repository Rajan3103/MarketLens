"""The order service: every trading signal passes through here before it reaches a broker.

    submit_signal(None, symbol="RELIANCE", action="BUY", qty=10, price=2501.5, reason="EMA cross")
    submit_signal("Nifty straddle", action="SELL", reason="09:20 entry",
                  legs=[{"symbol": "NIFTY 25000 CE 03-Nov-2026", "qty": 65, "price": 120},
                        {"symbol": "NIFTY 25000 PE 03-Nov-2026", "qty": 65, "price": 110}])
    submit_signal(4, symbol="RELIANCE", action="EXIT", reason="target hit")    # closes what strategy 4 holds

What happens to every signal
 1. It is saved in the signals table.
 2. Risk checks. Every leg must pass, otherwise NO leg is sent:
      kill switch, daily loss limit (MAX_DAILY_LOSS_RS), the strategy's loss limit, market open
      (09:15-15:30, Mon-Fri), Dhan login valid today (live only), lot-size multiples,
      max capital per trade (MAX_CAPITAL_PER_TRADE_RS) and MAX_OPEN_POSITIONS.
    An order that only reduces or closes a position skips the money rules (kill switch, losses, capital,
    positions), so you can always get out of a trade.
 3. Paper mode: sent at once through get_broker().
    Live mode with AUTO_TRADE=false: saved as WAITING_CONFIRMATION and a Telegram Confirm / Reject request
    is sent; confirm_signal() runs the risk checks again and sends it, reject_signal() cancels it.
    Live mode with AUTO_TRADE=true: sent at once.
    Every order waits for a free slot in the RateLimiter (MAX_ORDERS_PER_SECOND).
 4. Every step goes to the log file, orders to the orders table, messages to notifications and
    errors to error_log.

Kill switch: when today's P&L reaches -MAX_DAILY_LOSS_RS it switches on, blocks every new order for the rest
of the day and, with SQUARE_OFF_ON_KILL=true, closes all open positions. kill() / unkill() switch it by hand.
A new trading day starts with it off.

Command line (until the Telegram bot of module 12 answers the buttons itself):
    python -m marketlens.trading.order_service status
    python -m marketlens.trading.order_service confirm 12      # 12 = the signal number in the message
    python -m marketlens.trading.order_service reject 12
    python -m marketlens.trading.order_service kill [--square-off]
    python -m marketlens.trading.order_service unkill
"""
import argparse
import datetime as dt
import re
import time
from dataclasses import asdict, dataclass, field

import pandas as pd

from marketlens import config
from marketlens.broker.base import OrderResult, check_order
from marketlens.broker.candles import IST, MARKET_CLOSE, MARKET_OPEN
from marketlens.broker.paper import segment_for
from marketlens.logger import get_logger, record_error
from marketlens.utils import RateLimiter

log = get_logger(__name__)
WAITING = "WAITING_CONFIRMATION"
ACTIONS = ("BUY", "SELL", "EXIT")
SOURCES = ("backtest", "forward", "live", "webhook", "manual")
LIMITER = RateLimiter()                 # shared by every signal in this process
LOGIN_CACHE_SECONDS = 300               # ask Dhan about the login at most every 5 minutes
_login_cache: dict = {}


@dataclass
class Leg:
    """One order of a signal. side is BUY or SELL (EXIT is turned into one of them from the position)."""
    symbol: str
    side: str
    qty: int | None = None
    price: float | None = None           # MARKET: the latest price; LIMIT / SL: the limit price
    order_type: str = "MARKET"
    product: str = "INTRADAY"
    trigger_price: float | None = None
    security_id: str | None = None
    lot: int | None = None               # lot size (1 for shares)
    opens: bool = True                   # False = it only reduces or closes a position
    order_row: int | None = None         # its row in the orders table, once saved


@dataclass
class SignalResult:
    """What submit_signal / confirm_signal answer."""
    signal_id: int | None
    status: str                          # SENT, BLOCKED, WAITING_CONFIRMATION, CANCELLED or ERROR
    orders: list[OrderResult] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    message: str = ""


# ====================================================================== risk rules (pure, easy to test)

# True between 09:15 and 15:30 IST, Monday to Friday (exchange holidays are not known here).
def market_is_open(now: dt.datetime) -> bool:
    now = now.astimezone(IST)
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


# Rule: the market must be open.
def check_market_open(now: dt.datetime) -> str:
    if market_is_open(now):
        return ""
    return f"Market closed ({now.astimezone(IST):%a %d-%b %H:%M}): orders only 09:15-15:30, Monday to Friday"


# Rule: live orders need today's Dhan login (a token that lasts until 15:30).
def check_login(status, now: dt.datetime) -> str:
    if status is not None and status.valid_for_today(now):
        return ""
    why = status.message if status is not None else "not checked"
    return (f"Dhan login not valid for today ({why}): run `python -m marketlens.broker.session steps` "
            "for the daily login")


# Rule: F&O quantities must be whole lots (65, 130 ... for NIFTY). Shares have lot 1.
def check_lot(leg: Leg, lot: int | None) -> str:
    if lot is None:
        return f"{leg.symbol}: lot size unknown, so the quantity cannot be checked"
    if lot > 1 and leg.qty % lot:
        return f"{leg.symbol}: quantity {leg.qty} is not a multiple of the lot size {lot}"
    return ""


# Rule: one new order may use at most `limit` rupees (qty x price). 0 = no limit.
def check_capital(leg: Leg, price: float | None, limit: float) -> str:
    if not leg.opens or not limit:
        return ""
    if not price:
        return f"{leg.symbol}: no price, so the capital per trade cannot be checked (give the price)"
    value = leg.qty * price
    if value > limit:
        return (f"{leg.symbol}: {leg.qty} x Rs {price:,.2f} = Rs {value:,.0f} is above "
                f"MAX_CAPITAL_PER_TRADE_RS (Rs {limit:,.0f})")
    return ""


# Rule: at most `max_open` positions open at the same time (only orders that open a new symbol count).
def check_open_positions(new_symbols: set[str], open_symbols: set[str], max_open: int) -> str:
    adding = set(new_symbols) - set(open_symbols)
    if adding and len(open_symbols) + len(adding) > max_open:
        return (f"MAX_OPEN_POSITIONS ({max_open}) reached: {len(open_symbols)} open, "
                f"{len(adding)} more would be too many. Close a position first")
    return ""


# True when today's loss has reached the daily limit (limit 0 = no limit).
def daily_loss_hit(day_pnl: float, limit: float) -> bool:
    return bool(limit) and day_pnl <= -abs(limit)


# Rule: today's total P&L must be above -MAX_DAILY_LOSS_RS.
def check_daily_loss(day_pnl: float, limit: float) -> str:
    if daily_loss_hit(day_pnl, limit):
        return (f"Daily loss limit hit: today's P&L Rs {day_pnl:,.0f} (limit Rs {limit:,.0f}). "
                "Kill switch ON: no new orders today")
    return ""


# Rule: one strategy may lose at most `limit` rupees today (limit 0 = no limit).
def check_strategy_loss(pnl: float, limit: float, name: str) -> str:
    if limit and pnl <= -abs(limit):
        return f"Strategy '{name}' lost Rs {-pnl:,.0f} today (its limit is Rs {limit:,.0f}): no new orders for it"
    return ""


# Rule: no new orders while the kill switch is on.
def check_kill_switch(active: bool, reason: str = "") -> str:
    return f"Kill switch is ON ({reason}): no new orders today" if active else ""


# The strategy's own daily loss limit (params "max_loss_rs"), else MAX_STRATEGY_LOSS_RS.
def strategy_loss_limit(params: dict | None) -> float:
    value = (params or {}).get("max_loss_rs")
    try:
        return float(value) if value is not None else float(config.MAX_STRATEGY_LOSS_RS)
    except (TypeError, ValueError):
        return float(config.MAX_STRATEGY_LOSS_RS)


# ====================================================================== legs

# Turn the signal's inputs into legs and check them; raises ValueError with a simple reason.
def make_legs(action: str, symbol: str | None = None, qty: int | None = None, legs: list[dict] | None = None,
              price: float | None = None, order_type: str = "MARKET", product: str = "INTRADAY",
              trigger_price: float | None = None) -> list[Leg]:
    action = str(action).upper()
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, not {action!r}")
    if legs is None:
        if not symbol:
            raise ValueError("Give a symbol or a list of legs")
        legs = [{"symbol": symbol, "qty": qty, "price": price}]
    if not legs:
        raise ValueError("The list of legs is empty")
    out = []
    for d in legs:
        side = str(d.get("side") or d.get("action") or action).upper()
        if side not in ACTIONS:
            raise ValueError(f"side must be one of {ACTIONS}, not {side!r}")
        leg = Leg(str(d["symbol"]).strip(), side, d.get("qty"), d.get("price", price),
                  str(d.get("order_type", order_type)).upper(), str(d.get("product", product)).upper(),
                  d.get("trigger_price", trigger_price), d.get("security_id"))
        if not leg.symbol:
            raise ValueError("Every leg needs a symbol")
        if side != "EXIT":
            leg.qty = int(leg.qty) if leg.qty is not None and float(leg.qty) == int(float(leg.qty)) else leg.qty
            leg.side, leg.order_type, leg.product = check_order(side, leg.qty, leg.order_type, leg.price,
                                                                leg.product, leg.trigger_price)
        out.append(leg)
    return out


# Signed quantity held now for a leg's symbol, and that position's product.
def held_qty(positions: pd.DataFrame, leg: Leg) -> tuple[int, str | None]:
    if positions is None or positions.empty:
        return 0, None
    rows = positions[pd.to_numeric(positions["qty"], errors="coerce").fillna(0) != 0]
    same = rows["symbol"].astype(str) == leg.symbol
    if leg.security_id and "security_id" in rows:
        same |= rows["security_id"].astype(str) == str(leg.security_id)
    rows = rows[same]
    if rows.empty:
        return 0, None
    return int(pd.to_numeric(rows["qty"]).sum()), rows["product"].iloc[0]


# Symbols with an open position (qty not 0).
def open_symbols(positions: pd.DataFrame) -> set[str]:
    if positions is None or positions.empty:
        return set()
    rows = positions[pd.to_numeric(positions["qty"], errors="coerce").fillna(0) != 0]
    return set(rows["symbol"].astype(str))


# The lot size of a symbol: 1 for shares; F&O from Dhan's list (live) or the known index lots (paper).
def lot_size_for(symbol: str, broker=None) -> int | None:
    if segment_for(symbol) not in ("options", "futures"):
        return 1
    if getattr(broker, "mode", "paper") == "live":
        from marketlens.broker.dhan_broker import security_for
        return security_for(symbol, master=broker.master).lot_size
    from marketlens.options.chain import DEFAULT_LOT_SIZES
    underlying = re.split(r"[\s-]", symbol.strip().upper())[0]
    if underlying in DEFAULT_LOT_SIZES:
        return DEFAULT_LOT_SIZES[underlying]
    try:
        from marketlens.broker import dhan_instruments as di
        return di.lot_size(underlying)
    except Exception as err:                      # no instrument list (offline)
        log.warning("Lot size of %s unknown: %s", symbol, err)
        return None


# ====================================================================== kill switch (stored in the database)

# The start of the trading day `now` belongs to (midnight India time).
def day_start(now: dt.datetime) -> dt.datetime:
    return dt.datetime.combine(now.astimezone(IST).date(), dt.time(0), IST)


# Is the kill switch on today for this mode? Returns (active, reason).
def kill_switch_status(mode: str, now: dt.datetime, url: str | None = None) -> tuple[bool, str]:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import KillSwitchEvent
    with get_session(url) as session:
        row = session.scalars(select(KillSwitchEvent)
                              .where(KillSwitchEvent.mode == mode, KillSwitchEvent.ts >= day_start(now),
                                     KillSwitchEvent.ts <= now)
                              .order_by(KillSwitchEvent.ts.desc(), KillSwitchEvent.id.desc()).limit(1)).first()
    return (bool(row.active), row.reason) if row is not None else (False, "")


# Save one switch-on or switch-off of the kill switch.
def save_kill_switch(active: bool, reason: str, mode: str, now: dt.datetime, squared_off: bool = False,
                     url: str | None = None):
    from marketlens.db import get_session
    from marketlens.models import KillSwitchEvent
    with get_session(url) as session:
        session.add(KillSwitchEvent(ts=now, mode=mode, active=active, reason=reason, squared_off=squared_off))
        session.commit()


# Today's P&L per strategy from the positions table: positions still open or closed today.
def strategy_pnl_today(mode: str, now: dt.datetime, url: str | None = None) -> dict:
    from sqlalchemy import func, or_, select

    from marketlens.db import get_session
    from marketlens.models import Position
    net = Position.realized_pnl + func.coalesce(Position.unrealized_pnl, 0) - Position.costs
    with get_session(url) as session:
        rows = session.execute(select(Position.strategy_id, func.sum(net))
                               .where(Position.mode == mode,
                                      or_(Position.is_open, Position.closed_at >= day_start(now)))
                               .group_by(Position.strategy_id)).all()
    return {sid: float(total or 0) for sid, total in rows}


# ====================================================================== the service

class OrderService:
    """Everything submit_signal needs. Each piece can be swapped for a fake in tests:
    broker (one broker for every strategy), now (clock), notify(text, buttons) -> bool,
    login() -> LoginStatus, lots(symbol, broker) -> lot size, pnl(mode, broker) -> (total, {strategy: pnl}),
    limiter (RateLimiter)."""

    def __init__(self, url: str | None = None, broker=None, now=None, notify=None, login=None, lots=None,
                 pnl=None, limiter: RateLimiter | None = None):
        self.url, self.broker = url, broker
        self.now = now or (lambda: dt.datetime.now(IST))
        self.notify = notify or _telegram
        self.login = login or (lambda: cached_login(url))
        self.lots = lots or lot_size_for
        self.pnl = pnl or self._pnl_today
        self.limiter = limiter or LIMITER

    # ------------------------------------------------------------ main entry

    # Save the signal, check the risk rules and send it (or ask for your confirmation).
    def submit_signal(self, strategy=None, action: str = "BUY", symbol: str | None = None, qty: int | None = None,
                      legs: list[dict] | None = None, reason: str = "", price: float | None = None,
                      order_type: str = "MARKET", product: str = "INTRADAY", trigger_price: float | None = None,
                      source: str = "manual") -> SignalResult:
        """strategy: its id, its name or None (your own trades). qty is in units (lots x lot size)."""
        mode, now = config.TRADING_MODE, self.now()
        sid, name, params = self.strategy_info(strategy)
        legs = make_legs(action, symbol, qty, legs, price, order_type, product, trigger_price)
        signal_id = self.save_signal(sid, name, str(action).upper(), legs, reason, source, mode, now)
        log.info("Signal %s (%s, %s mode): %s %s - %s", signal_id, name, mode, str(action).upper(),
                 ", ".join(f"{l.side} {l.symbol} x{l.qty or 'all'}" for l in legs), reason or "no reason given")
        try:
            broker = self.broker_for(sid)
            problems = self.prepare_legs(legs, broker)
            problems += self.risk_problems(legs, broker, sid, name, params, mode, now)
        except Exception as err:
            message = f"Signal {signal_id} could not be checked: {_clean(err)}"
            record_error(__name__, message, {"signal_id": signal_id, "strategy": name}, url=self.url)
            self.save_orders(legs, mode, sid, signal_id, "REJECTED", message)
            self.tell(f"⚠️ {message}")
            return SignalResult(signal_id, "ERROR", message=message)
        if problems:
            return self.block(legs, problems, mode, sid, signal_id, name)
        if mode == "live" and not config.AUTO_TRADE:
            return self.ask_confirmation(legs, sid, signal_id, name, reason)
        results = self.send(legs, broker, mode, sid, signal_id)
        return self.finish(signal_id, name, legs, results, mode)

    # ------------------------------------------------------------ inputs

    # (id, name, params) of the strategy given by id or name; (None, "manual", {}) for your own trades.
    def strategy_info(self, strategy) -> tuple[int | None, str, dict]:
        if strategy is None:
            return None, "manual", {}
        from sqlalchemy import func, select

        from marketlens.db import get_session
        from marketlens.models import Strategy
        with get_session(self.url) as session:
            if isinstance(strategy, int):
                row = session.get(Strategy, strategy)
            else:
                row = session.scalar(select(Strategy).where(func.lower(Strategy.name) == str(strategy).lower()))
            if row is None:
                raise ValueError(f"Strategy {strategy!r} is not in the Strategy Library")
            return row.id, row.name, dict(row.params or {})

    # The broker for a strategy: the fake one in tests, else get_broker() (live: the service saves the orders).
    def broker_for(self, strategy_id: int | None):
        if self.broker is not None:
            return self.broker
        from marketlens.broker.factory import get_broker
        if config.TRADING_MODE == "live":
            return get_broker(strategy_id=strategy_id, url=self.url, save=False)
        return get_broker(strategy_id=strategy_id, url=self.url)

    # Fill in what each leg needs from the broker: Security ID, lot size, EXIT side/qty, opens or reduces.
    # Returns problems found on the way (e.g. nothing to exit).
    def prepare_legs(self, legs: list[Leg], broker) -> list[str]:
        problems = []
        if broker.mode == "live":
            from marketlens.broker.dhan_broker import security_for
            for leg in legs:
                leg.security_id = str(security_for(leg.symbol, leg.security_id, broker.master).security_id)
        positions = broker.get_positions()
        for leg in legs:
            held, held_product = held_qty(positions, leg)
            if leg.side == "EXIT":
                if held == 0:
                    problems.append(f"Nothing to exit: no open {leg.symbol} position")
                    leg.side, leg.qty, leg.opens, leg.lot = "SELL", leg.qty or 0, False, 1
                    continue
                leg.side = "SELL" if held > 0 else "BUY"
                leg.qty = min(int(leg.qty), abs(held)) if leg.qty else abs(held)
                leg.product = held_product or leg.product
            signed = leg.qty if leg.side == "BUY" else -leg.qty
            leg.opens = held == 0 or (held > 0) == (signed > 0) or abs(signed) > abs(held)
            leg.lot = self.lots(leg.symbol, broker)
        return problems

    # ------------------------------------------------------------ risk checks

    # Every rule for these legs; an empty list means all passed.
    def risk_problems(self, legs: list[Leg], broker, sid: int | None, name: str, params: dict, mode: str,
                      now: dt.datetime) -> list[str]:
        problems = []
        opening = [leg for leg in legs if leg.opens]
        if opening:
            active, why = kill_switch_status(mode, now, self.url)
            day_pnl, by_strategy = self.pnl(mode, broker)
            if active:
                problems.append(check_kill_switch(True, why))
            elif daily_loss_hit(day_pnl, config.MAX_DAILY_LOSS_RS):
                problems.append(check_daily_loss(day_pnl, config.MAX_DAILY_LOSS_RS))
                self.trip_kill_switch(day_pnl, mode, now)
            if sid is not None:
                problems.append(check_strategy_loss(by_strategy.get(sid, 0.0), strategy_loss_limit(params), name))
        problems.append(check_market_open(now))
        if mode == "live":
            problems.append(check_login(self.login(), now))
        for leg in legs:
            problems.append(check_lot(leg, leg.lot))
        for leg in opening:
            problems.append(check_capital(leg, leg.price or _ltp(broker, leg), config.MAX_CAPITAL_PER_TRADE_RS))
        positions = broker.get_positions()
        problems.append(check_open_positions({leg.symbol for leg in opening}, open_symbols(positions),
                                             config.MAX_OPEN_POSITIONS))
        return [p for p in problems if p]

    # Today's P&L: total and per strategy. Live totals come from Dhan's positions (today's numbers).
    def _pnl_today(self, mode: str, broker) -> tuple[float, dict]:
        by_strategy = strategy_pnl_today(mode, self.now(), self.url)
        if mode == "live":
            positions = broker.get_positions()
            total = float(pd.to_numeric(positions.get("realized_pnl"), errors="coerce").fillna(0).sum()
                          + pd.to_numeric(positions.get("unrealized_pnl"), errors="coerce").fillna(0).sum()) \
                if not positions.empty else 0.0
            return total, by_strategy
        return sum(by_strategy.values()), by_strategy

    # ------------------------------------------------------------ outcomes

    # A signal that failed a rule: every leg is saved as REJECTED with the reasons, and you are told.
    def block(self, legs: list[Leg], problems: list[str], mode: str, sid, signal_id: int, name: str) -> SignalResult:
        reasons = "; ".join(problems)
        self.save_orders(legs, mode, sid, signal_id, "REJECTED", reasons)
        log.warning("Signal %s (%s) blocked: %s", signal_id, name, reasons)
        self.tell(f"🚫 Signal {signal_id} ({name}) blocked, nothing was sent:\n- " + "\n- ".join(problems))
        return SignalResult(signal_id, "BLOCKED", problems=problems, message=reasons)

    # Live with AUTO_TRADE=false: save the orders as waiting and ask on Telegram.
    def ask_confirmation(self, legs: list[Leg], sid, signal_id: int, name: str, reason: str) -> SignalResult:
        self.save_orders(legs, "live", sid, signal_id, WAITING, "Waiting for your Confirm on Telegram")
        lines = "\n".join(f"- {l.side} {l.qty} {l.symbol} {l.order_type}" + (f" @ {l.price}" if l.price else "")
                          for l in legs)
        text = (f"🔔 LIVE order needs your OK. Signal {signal_id} ({name}): {reason or 'no reason given'}\n{lines}\n"
                f"Tap Confirm within {config.CONFIRM_TIMEOUT_MIN} minutes, or run:\n"
                f"python -m marketlens.trading.order_service confirm {signal_id}")
        self.tell(text, buttons=[("✅ Confirm", f"confirm:{signal_id}"), ("❌ Reject", f"reject:{signal_id}")])
        log.info("Signal %s waiting for confirmation (%s legs)", signal_id, len(legs))
        return SignalResult(signal_id, WAITING, message="Waiting for your confirmation on Telegram")

    # Send each leg through the broker (waiting for the rate limiter) and save the outcome.
    def send(self, legs: list[Leg], broker, mode: str, sid, signal_id: int) -> list[OrderResult]:
        results = []
        for leg in legs:
            self.limiter.wait()
            try:
                if broker.mode == "paper" and leg.price and leg.order_type == "MARKET":
                    broker.update_price(leg.symbol, float(leg.price))   # the signal's price is the latest price
                limit_price = None if leg.order_type in ("MARKET", "SL-M") else leg.price
                result = broker.place_order(leg.symbol, leg.side, int(leg.qty), leg.order_type, price=limit_price,
                                            product=leg.product, tag=f"signal:{signal_id}",
                                            security_id=leg.security_id, trigger_price=leg.trigger_price)
            except Exception as err:
                message = f"{leg.side} {leg.qty} {leg.symbol} failed: {_clean(err)}"
                record_error(__name__, f"Signal {signal_id}: {message}", {"signal_id": signal_id}, url=self.url)
                result = OrderResult(None, "REJECTED", message=message)
            self.record_sent(leg, result, broker, mode, sid, signal_id)
            log.info("Signal %s: %s %s %s x%s -> %s %s", signal_id, mode, leg.side, leg.symbol, leg.qty,
                     result.status, result.message)
            results.append(result)
        return results

    # Tell you how the sent signal went; returns its SignalResult.
    def finish(self, signal_id: int, name: str, legs: list[Leg], results: list[OrderResult], mode: str) -> SignalResult:
        bad = [r for r in results if r.status == "REJECTED"]
        lines = "\n".join(f"- {l.side} {l.qty} {l.symbol}: {r.status}"
                          + (f" @ {r.avg_price:,.2f}" if r.avg_price else "") + (f" ({r.message})" if r.message else "")
                          for l, r in zip(legs, results))
        icon = "⚠️" if bad else "✅"
        self.tell(f"{icon} {mode.upper()} signal {signal_id} ({name}) sent:\n{lines}")
        if bad and len(bad) < len(results):
            record_error(__name__, f"Signal {signal_id}: only some legs were accepted; check your positions",
                         {"signal_id": signal_id}, url=self.url, level="WARNING")
        return SignalResult(signal_id, "SENT", results, [r.message for r in bad],
                            "Some legs were rejected" if bad else "All legs sent")

    # ------------------------------------------------------------ confirmation

    # Your Confirm: run the risk checks again and send the waiting orders (cancelled when too late).
    def confirm_signal(self, signal_id: int) -> SignalResult:
        rows = self.waiting_orders(signal_id)
        if not rows:
            return SignalResult(signal_id, "ERROR", message=f"No orders of signal {signal_id} are waiting")
        now, sid = self.now(), rows[0]["strategy_id"]
        age = (now - rows[0]["placed_at"]).total_seconds() / 60
        if config.TRADING_MODE != "live":
            return self.cancel_waiting(signal_id, "TRADING_MODE is no longer live: cancelled")
        if age > config.CONFIRM_TIMEOUT_MIN:
            return self.cancel_waiting(signal_id, f"Confirmed after {age:.0f} minutes "
                                                  f"(limit {config.CONFIRM_TIMEOUT_MIN}): cancelled, prices have moved")
        _, name, params = self.strategy_info(sid)
        legs = [Leg(r["symbol"], r["side"], r["qty"], r["price"], r["order_type"], r["product"],
                    r["trigger_price"], r["security_id"], order_row=r["id"]) for r in rows]
        try:
            broker = self.broker_for(sid)
            problems = self.prepare_legs(legs, broker) + self.risk_problems(legs, broker, sid, name, params, "live", now)
        except Exception as err:
            return self.cancel_waiting(signal_id, f"Could not check the risk rules: {_clean(err)}", error=True)
        if problems:
            self.update_orders(legs, "REJECTED", "; ".join(problems))
            self.tell(f"🚫 Signal {signal_id} confirmed but blocked now:\n- " + "\n- ".join(problems))
            return SignalResult(signal_id, "BLOCKED", problems=problems, message="; ".join(problems))
        log.info("Signal %s confirmed by you: sending %s legs", signal_id, len(legs))
        results = self.send(legs, broker, "live", sid, signal_id)
        return self.finish(signal_id, name, legs, results, "live")

    # Your Reject: cancel the waiting orders.
    def reject_signal(self, signal_id: int) -> SignalResult:
        if not self.waiting_orders(signal_id):
            return SignalResult(signal_id, "ERROR", message=f"No orders of signal {signal_id} are waiting")
        return self.cancel_waiting(signal_id, "Rejected by you")

    # Cancel the waiting orders of a signal with a reason.
    def cancel_waiting(self, signal_id: int, reason: str, error: bool = False) -> SignalResult:
        legs = [Leg(r["symbol"], r["side"], r["qty"], order_row=r["id"]) for r in self.waiting_orders(signal_id)]
        self.update_orders(legs, "CANCELLED", reason)
        if error:
            record_error(__name__, f"Signal {signal_id}: {reason}", {"signal_id": signal_id}, url=self.url)
        log.info("Signal %s cancelled: %s", signal_id, reason)
        self.tell(f"❌ Signal {signal_id} cancelled: {reason}")
        return SignalResult(signal_id, "CANCELLED", message=reason)

    # The orders of a signal still waiting for your confirmation (oldest first).
    def waiting_orders(self, signal_id: int) -> list[dict]:
        from sqlalchemy import select

        from marketlens.db import get_session
        from marketlens.models import Order
        with get_session(self.url) as session:
            rows = session.scalars(select(Order).where(Order.signal_id == signal_id, Order.status == WAITING)
                                   .order_by(Order.id)).all()
            return [{"id": r.id, "strategy_id": r.strategy_id, "symbol": r.symbol, "side": r.side, "qty": r.qty,
                     "price": float(r.price) if r.price is not None else None, "order_type": r.order_type,
                     "product": r.product, "security_id": r.security_id,
                     "trigger_price": float(r.trigger_price) if r.trigger_price is not None else None,
                     "placed_at": r.placed_at} for r in rows]

    # ------------------------------------------------------------ kill switch

    # The daily loss limit was hit: switch the kill switch on, tell you and close everything if you chose so.
    def trip_kill_switch(self, day_pnl: float, mode: str, now: dt.datetime):
        reason = f"daily loss Rs {-day_pnl:,.0f} reached MAX_DAILY_LOSS_RS Rs {config.MAX_DAILY_LOSS_RS:,.0f}"
        self.kill(reason, square_off=config.SQUARE_OFF_ON_KILL, mode=mode, now=now)

    # Switch the kill switch on by hand (or from the loss limit); square_off=True closes every open position.
    def kill(self, reason: str = "switched on by you", square_off: bool = False, mode: str | None = None,
             now: dt.datetime | None = None) -> str:
        mode, now = mode or config.TRADING_MODE, now or self.now()
        closed = ""
        if square_off:
            try:
                closed = self.square_off_all(mode)
            except Exception as err:
                closed = f"square-off FAILED: {_clean(err)}. Close your positions on Dhan / the app yourself"
                record_error(__name__, f"Kill switch {closed}", {"mode": mode}, url=self.url)
        save_kill_switch(True, reason, mode, now, squared_off=square_off and "FAILED" not in closed, url=self.url)
        log.warning("KILL SWITCH ON (%s): %s %s", mode, reason, closed)
        record_error(__name__, f"Kill switch ON ({mode}): {reason}", {"square_off": square_off}, url=self.url,
                     level="WARNING")
        self.tell(f"🛑 KILL SWITCH ON ({mode}): {reason}. No new orders today."
                  + (f"\nSquare-off: {closed}" if square_off else "\nOpen positions were NOT closed."))
        return closed

    # Switch the kill switch off again (new orders are allowed).
    def unkill(self, reason: str = "switched off by you", mode: str | None = None) -> None:
        mode = mode or config.TRADING_MODE
        save_kill_switch(False, reason, mode, self.now(), url=self.url)
        log.warning("Kill switch OFF (%s): %s", mode, reason)
        self.tell(f"🟢 Kill switch OFF ({mode}): {reason}")

    # Close every open position (live: Dhan's exit-all; paper: a market order against each position).
    def square_off_all(self, mode: str) -> str:
        if mode == "live":
            return self.broker_for(None).exit_all()
        done = []
        for sid in self.paper_strategies_with_positions():
            broker = self.broker_for(sid)
            positions = broker.get_positions()
            for row in positions[pd.to_numeric(positions["qty"], errors="coerce").fillna(0) != 0].itertuples():
                price = row.ltp if pd.notna(row.ltp) and row.ltp else row.avg_price
                broker.update_price(row.symbol, float(price))
                self.limiter.wait()
                result = broker.place_order(row.symbol, "SELL" if row.qty > 0 else "BUY", abs(int(row.qty)),
                                            product=row.product, tag="kill switch")
                done.append(f"{row.symbol} {result.status}")
        return ", ".join(done) or "no open positions"

    # Strategy ids (None = your own trades) with open paper positions; the fake broker in tests counts as one.
    def paper_strategies_with_positions(self) -> list:
        if self.broker is not None:
            return [None]
        from sqlalchemy import select

        from marketlens.db import get_session
        from marketlens.models import Position
        with get_session(self.url) as session:
            return list(session.scalars(select(Position.strategy_id).distinct()
                                        .where(Position.mode == "paper", Position.is_open)))

    # ------------------------------------------------------------ saving

    # Save the signal in the signals table; returns its id.
    def save_signal(self, sid, name: str, action: str, legs: list[Leg], reason: str, source: str, mode: str,
                    now: dt.datetime) -> int:
        from marketlens.db import get_session
        from marketlens.models import Signal
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        legs_json = [{k: v for k, v in asdict(l).items() if k in ("symbol", "side", "qty", "price", "order_type",
                                                                   "product", "trigger_price")} for l in legs]
        with get_session(self.url) as session:
            row = Signal(ts=now, strategy_id=sid, side=action, source=source, reason=reason or None,
                         price=legs[0].price if len(legs) == 1 else None,
                         params={"mode": mode, "strategy": name, "legs": legs_json})
            session.add(row)
            session.commit()
            return row.id

    # Save legs that were not sent to a broker (REJECTED or WAITING_CONFIRMATION) in the orders table.
    def save_orders(self, legs: list[Leg], mode: str, sid, signal_id: int, status: str, message: str):
        from marketlens.db import get_session
        from marketlens.models import Order
        with get_session(self.url) as session:
            rows = [Order(mode=mode, signal_id=signal_id, strategy_id=sid, symbol=leg.symbol,
                          security_id=leg.security_id, segment=segment_for(leg.symbol, leg.product),
                          tag=f"signal:{signal_id}", side=leg.side if leg.side in ("BUY", "SELL") else "SELL",
                          order_type=leg.order_type, product=leg.product, qty=max(int(leg.qty or 0), 0),
                          price=leg.price, trigger_price=leg.trigger_price, status=status,
                          error_message=message[:2000], placed_at=self.now()) for leg in legs]
            session.add_all(rows)
            session.commit()
            for leg, row in zip(legs, rows):
                leg.order_row = row.id

    # Change the status of saved orders (confirm / reject / timeout).
    def update_orders(self, legs: list[Leg], status: str, message: str | None = None,
                      broker_ids: list | None = None):
        from marketlens.db import get_session
        from marketlens.models import Order
        with get_session(self.url) as session:
            for i, leg in enumerate(legs):
                row = session.get(Order, leg.order_row)
                if row is None:
                    continue
                row.status, row.error_message = status, (message or None) and message[:2000]
                if broker_ids and broker_ids[i] is not None:
                    row.broker_order_id = str(broker_ids[i])
            session.commit()

    # After sending: paper orders are saved by the paper broker (link them to the signal);
    # live orders (and failures) are saved or updated here.
    def record_sent(self, leg: Leg, result: OrderResult, broker, mode: str, sid, signal_id: int):
        from marketlens.db import get_session
        from marketlens.models import Order
        with get_session(self.url) as session:
            if leg.order_row is None and broker.mode == "paper" and getattr(broker, "save", False) \
                    and isinstance(result.order_id, int):
                paper_row = session.get(Order, result.order_id)        # saved by the paper broker
                if paper_row is not None:
                    paper_row.signal_id = signal_id
                    session.commit()
                    leg.order_row = paper_row.id
                    return
            row = session.get(Order, leg.order_row) if leg.order_row is not None else None   # a confirmed order
            if row is None:
                row = Order(mode=mode, strategy_id=sid, symbol=leg.symbol, security_id=leg.security_id,
                            segment=segment_for(leg.symbol, leg.product), tag=f"signal:{signal_id}",
                            side=leg.side, order_type=leg.order_type, product=leg.product, qty=int(leg.qty),
                            price=leg.price, trigger_price=leg.trigger_price, placed_at=self.now())
                session.add(row)
            row.signal_id, row.status = signal_id, result.status
            if mode == "live" and result.order_id is not None:
                row.broker_order_id = str(result.order_id)
            if result.avg_price:
                row.avg_fill_price, row.filled_qty = result.avg_price, result.filled_qty
            row.error_message = (result.message or "")[:2000] or None
            session.commit()
            leg.order_row = row.id

    # Send a message (Telegram) and keep a copy in the notifications table. Never raises.
    def tell(self, text: str, buttons: list[tuple[str, str]] | None = None) -> bool:
        try:
            ok = bool(self.notify(text, buttons=buttons))
        except Exception as err:
            ok = False
            log.warning("Notification failed: %s", _clean(err))
        try:
            from marketlens.db import get_session
            from marketlens.models import Notification
            with get_session(self.url) as session:
                session.add(Notification(channel="telegram", message=text, status="sent" if ok else "failed",
                                         error=None if ok else "Not sent (Telegram key not set or Telegram error)",
                                         sent_at=self.now() if ok else None))
                session.commit()
        except Exception as err:
            log.warning("Could not save the notification: %s", err)
        return ok


# ====================================================================== helpers

# The broker's latest price for a leg (paper broker), or None.
def _ltp(broker, leg: Leg) -> float | None:
    ltp = getattr(broker, "ltp", None)
    try:
        return ltp(leg.symbol, leg.security_id) if callable(ltp) else None
    except Exception:
        return None


# An error as safe text (secrets removed).
def _clean(err) -> str:
    from marketlens.broker.dhan_broker import scrub
    return scrub(err)


# The real Telegram sender (tests pass their own notify).
def _telegram(text: str, buttons=None) -> bool:
    from marketlens.alerts.telegram import send_message
    return send_message(text, buttons=buttons)


# Today's Dhan login status, asked again at most every 5 minutes.
def cached_login(url: str | None = None):
    from marketlens.broker.session import check_login as dhan_login
    if _login_cache and time.monotonic() - _login_cache["at"] < LOGIN_CACHE_SECONDS:
        return _login_cache["status"]
    status = dhan_login(save=True, url=url)
    _login_cache.update(status=status, at=time.monotonic())
    return status


# Shortcut: OrderService().submit_signal(...).
def submit_signal(strategy=None, action: str = "BUY", symbol: str | None = None, qty: int | None = None,
                  legs: list[dict] | None = None, reason: str = "", url: str | None = None, **kwargs) -> SignalResult:
    return OrderService(url=url).submit_signal(strategy, action, symbol, qty, legs, reason, **kwargs)


# Command line: status, confirm N, reject N, kill [--square-off], unkill.
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MarketLens order service")
    parser.add_argument("command", choices=["status", "confirm", "reject", "kill", "unkill"])
    parser.add_argument("signal_id", nargs="?", type=int, help="the signal number (confirm / reject)")
    parser.add_argument("--square-off", action="store_true", help="kill: also close every open position")
    args = parser.parse_args(argv)
    service = OrderService()
    if args.command in ("confirm", "reject"):
        if args.signal_id is None:
            parser.error("give the signal number, e.g. confirm 12")
        result = (service.confirm_signal if args.command == "confirm" else service.reject_signal)(args.signal_id)
        print(f"{result.status}: {result.message}")
        return 0 if result.status in ("SENT", "CANCELLED") else 1
    if args.command == "kill":
        print(service.kill(square_off=args.square_off) or "Kill switch ON")
    elif args.command == "unkill":
        service.unkill()
        print("Kill switch OFF")
    active, why = kill_switch_status(config.TRADING_MODE, service.now())
    print(f"Mode {config.TRADING_MODE}, AUTO_TRADE={config.AUTO_TRADE}, kill switch "
          f"{'ON (' + why + ')' if active else 'off'}, daily loss limit Rs {config.MAX_DAILY_LOSS_RS:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
