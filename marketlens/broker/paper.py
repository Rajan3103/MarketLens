"""Paper broker: practise with virtual money. Same methods as a real broker (see base.py).

    broker = PaperBroker()                              # capital = PAPER_CAPITAL from .env
    broker.update_price("RELIANCE", 2500)               # the latest price (from live data or a replay)
    broker.place_order("RELIANCE", "BUY", 10)           # market: fills now at 2500 + slippage
    broker.place_order("RELIANCE", "SELL", 10, "LIMIT", price=2600)   # waits...
    broker.update_price("RELIANCE", 2601)               # ...price crossed 2600: the limit order fills

Rules
  - MARKET fills at the latest price plus slippage (buy higher, sell lower).
  - LIMIT fills when the price reaches the limit, at the limit or better (no slippage).
  - SL-M (stop-market) becomes a market order when the price reaches the trigger; SL (stop-limit)
    becomes a limit order at `price` when the trigger is reached.
  - Brokerage and taxes come from costs.py for every fill.
  - Positions are netted per symbol and product. Realised P&L uses the average price:
    buy 10 at 100, sell 10 at 110 -> +100 before costs.
  - Every order and position is saved in the orders / positions tables with mode='paper', and is
    loaded again when the app restarts.
  - Funds are simple: money used = |qty| x average price of open positions (a short option needs far
    more margin at a real broker; the paper broker does not model that).
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from marketlens import config
from marketlens.broker.base import OPEN_STATUSES, Broker, OrderResult, check_order
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings, apply_slippage, to_paise, trade_costs
from marketlens.logger import get_logger

log = get_logger(__name__)
ORDER_COLUMNS = ["id", "placed_at", "symbol", "side", "qty", "order_type", "product", "price", "trigger_price",
                 "status", "filled_qty", "avg_fill_price", "costs", "filled_at", "tag", "message"]
POSITION_COLUMNS = ["id", "symbol", "product", "qty", "avg_price", "ltp", "realized_pnl", "unrealized_pnl",
                    "costs", "net_pnl", "is_open", "opened_at", "closed_at"]


# The costs.py segment for a symbol: options, futures, or equity (delivery for CNC, else intraday).
def segment_for(symbol: str, product: str = "INTRADAY") -> str:
    words = symbol.upper().split()
    if "CE" in words or "PE" in words or symbol.upper().endswith(("CE", "PE")) and any(c.isdigit() for c in symbol):
        return "options"
    if "FUT" in symbol.upper():
        return "futures"
    return "equity_delivery" if product.upper() == "CNC" else "equity_intraday"


# The time now in India.
def _now() -> dt.datetime:
    return dt.datetime.now(IST)


@dataclass
class PaperOrder:
    """One virtual order."""
    id: int | None
    symbol: str
    side: str
    qty: int
    order_type: str
    product: str
    segment: str
    price: float | None = None
    trigger_price: float | None = None
    security_id: str | None = None
    tag: str | None = None
    status: str = "PENDING"
    filled_qty: int = 0
    avg_price: float | None = None
    costs: float = 0.0
    placed_at: dt.datetime = field(default_factory=_now)
    filled_at: dt.datetime | None = None
    message: str = ""
    triggered: bool = False           # SL / SL-M: the trigger price has been reached

    # The answer place_order / cancel_order give back.
    def result(self) -> OrderResult:
        return OrderResult(self.id, self.status, self.filled_qty, self.avg_price, self.costs, self.message)


@dataclass
class PaperPosition:
    """Net position in one symbol and product. qty below 0 = short."""
    id: int | None
    symbol: str
    product: str
    segment: str
    security_id: str | None = None
    qty: int = 0
    avg_price: float = 0.0
    realized: float = 0.0             # before costs
    costs: float = 0.0
    ltp: float | None = None
    opened_at: dt.datetime = field(default_factory=_now)
    closed_at: dt.datetime | None = None

    # True while some quantity is held.
    @property
    def is_open(self) -> bool:
        return self.qty != 0

    # P&L of the open quantity at the latest price.
    @property
    def unrealized(self) -> float:
        return to_paise(self.qty * (self.ltp - self.avg_price)) if self.qty and self.ltp is not None else 0.0

    # Add a fill: average in when adding, realise P&L when reducing, flip when going past zero.
    def apply(self, side: str, qty: int, price: float, costs: float):
        signed = qty if side == "BUY" else -qty
        self.costs = to_paise(self.costs + costs)
        if self.qty == 0 or (self.qty > 0) == (signed > 0):         # opening or adding
            total = abs(self.qty) + qty
            self.avg_price = to_paise((abs(self.qty) * self.avg_price + qty * price) / total)
            self.qty += signed
            return
        closing = min(abs(self.qty), qty)                            # reducing (maybe flipping)
        direction = 1 if self.qty > 0 else -1
        self.realized = to_paise(self.realized + closing * (price - self.avg_price) * direction)
        self.qty += signed
        if self.qty != 0 and (self.qty > 0) != (direction > 0):      # went past zero: new position at this price
            self.avg_price = price
        elif self.qty == 0:
            self.closed_at = _now()


class PaperBroker(Broker):
    """Virtual broker. prices(symbol, security_id) may return the latest price; otherwise use update_price()."""
    mode = "paper"

    def __init__(self, prices: Callable[[str, str | None], float | None] | None = None, capital: float | None = None,
                 costs: CostSettings | None = None, save: bool = True, url: str | None = None,
                 max_open_positions: int | None = None):
        self.prices, self.costs, self.save, self.url = prices, costs or CostSettings(), save, url
        self.no_slip = CostSettings(**{**self.costs.__dict__, "slippage_value": 0.0})   # slippage is in the price
        self.capital = float(config.PAPER_CAPITAL if capital is None else capital)
        self.max_open = max_open_positions or config.MAX_OPEN_POSITIONS
        self.last: dict[str, float] = {}
        self.orders: list[PaperOrder] = []                 # this session's orders + pending ones loaded back
        self.positions: dict[tuple[str, str], PaperPosition] = {}   # open positions by (symbol, product)
        self.closed: list[PaperPosition] = []              # closed in this session (not saved)
        self.closed_realized = self.closed_costs = 0.0     # closed earlier (from the database)
        self._next_id = 1
        if save:
            self._load()

    # ---- prices ----

    # The latest price of a symbol, or None.
    def ltp(self, symbol: str, security_id: str | None = None) -> float | None:
        if self.prices is not None:
            price = self.prices(symbol, security_id)
            if price is not None:
                self.last[symbol] = float(price)
        return self.last.get(symbol)

    # A new price for a symbol: pending orders for it are checked and may fill.
    def update_price(self, symbol: str, price: float) -> list[OrderResult]:
        if price is None or price <= 0:
            raise ValueError("price must be above 0")
        self.last[symbol] = float(price)
        pos = self.positions.get
        for product in ("INTRADAY", "MARGIN", "CNC"):
            if pos((symbol, product)):
                pos((symbol, product)).ltp = float(price)
        return [o.result() for o in list(self.orders)
                if o.symbol == symbol and o.status in OPEN_STATUSES and self._try_fill(o, float(price))]

    # ---- orders ----

    # Place an order; market orders fill at once, others wait for the price (see the module notes).
    def place_order(self, symbol: str, side: str, qty: int, order_type: str = "MARKET",
                    price: float | None = None, product: str = "INTRADAY", tag: str | None = None,
                    security_id: str | None = None, trigger_price: float | None = None,
                    segment: str | None = None) -> OrderResult:
        side, order_type, product = check_order(side, qty, order_type, price, product, trigger_price)
        order = PaperOrder(None, symbol, side, qty, order_type, product, segment or segment_for(symbol, product),
                           price=price, trigger_price=trigger_price, security_id=security_id, tag=tag)
        self.orders.append(order)
        ltp = self.ltp(symbol, security_id)
        problem = self._risk_problem(order, ltp)
        if problem:
            order.status, order.message = "REJECTED", problem
        else:
            order.status = "PLACED"
            self._try_fill(order, ltp)
        self._save_order(order)
        log.info("Paper order %s %s %s x%s: %s %s", order.id, side, symbol, qty, order.status, order.message)
        return order.result()

    # Cancel an order that is still waiting.
    def cancel_order(self, order_id) -> OrderResult:
        order = next((o for o in self.orders if o.id == order_id), None)
        if order is None:
            return OrderResult(order_id, "REJECTED", message=f"No open paper order {order_id}")
        if order.status not in OPEN_STATUSES:
            return OrderResult(order_id, order.status, order.filled_qty, order.avg_price, order.costs,
                               f"Order is already {order.status}; it cannot be cancelled")
        order.status, order.message = "CANCELLED", "Cancelled by you"
        self._save_order(order)
        return order.result()

    # Why an order must be rejected (no price, too many positions, not enough virtual money), or "".
    def _risk_problem(self, order: PaperOrder, ltp: float | None) -> str:
        if order.order_type in ("MARKET", "SL-M") and ltp is None:
            return f"No price for {order.symbol} yet: call update_price() or give a price feed"
        held = self.positions.get((order.symbol, order.product))
        signed = order.qty if order.side == "BUY" else -order.qty
        adds = held is None or not held.is_open or (held.qty > 0) == (signed > 0) or abs(signed) > abs(held.qty)
        if not adds:
            return ""                                                    # reducing a position is always allowed
        if (held is None or not held.is_open) and self.open_count() >= self.max_open:
            return f"MAX_OPEN_POSITIONS ({self.max_open}) reached: close a position first"
        price = order.price or ltp or order.trigger_price
        needed = order.qty * price
        available = self.get_funds()["available"]
        if needed > available:
            return f"Not enough virtual funds: needs Rs {needed:,.0f}, available Rs {available:,.0f}"
        return ""

    # Fill the order if the price allows; returns True when it filled.
    def _try_fill(self, order: PaperOrder, price: float | None) -> bool:
        if price is None or order.status not in OPEN_STATUSES:
            return False
        kind = order.order_type
        if kind in ("SL", "SL-M") and not order.triggered:
            reached = price >= order.trigger_price if order.side == "BUY" else price <= order.trigger_price
            if not reached:
                return False
            order.triggered = True
        if kind in ("MARKET", "SL-M"):
            fill = apply_slippage(price, order.side, self.costs)
        else:                                                       # LIMIT, or SL after its trigger
            crossed = price <= order.price if order.side == "BUY" else price >= order.price
            if not crossed:
                if kind == "SL" and order.triggered:
                    self._save_order(order)
                return False
            fill = min(price, order.price) if order.side == "BUY" else max(price, order.price)
        self._fill(order, to_paise(fill))
        return True

    # Record a fill: costs from costs.py, then update the position.
    def _fill(self, order: PaperOrder, fill: float):
        costs = trade_costs(order.segment, order.side, fill, order.qty, self.no_slip).total
        order.status, order.filled_qty, order.avg_price, order.costs = "FILLED", order.qty, fill, costs
        order.filled_at = _now()
        key = (order.symbol, order.product)
        pos = self.positions.get(key)
        if pos is None:
            pos = PaperPosition(None, order.symbol, order.product, order.segment, order.security_id)
            self.positions[key] = pos
        pos.ltp = self.last.get(order.symbol, fill)
        pos.apply(order.side, order.qty, fill, costs)
        self._save_position(pos)
        if not pos.is_open:
            self.closed.append(self.positions.pop(key))
        self._save_order(order)

    # ---- reports ----

    # Number of open positions.
    def open_count(self) -> int:
        return sum(p.is_open for p in self.positions.values())

    # Orders, newest first (from the database when saving, else this session's).
    def get_orders(self) -> pd.DataFrame:
        if self.save:
            return self._db_orders()
        rows = [{"id": o.id, "placed_at": o.placed_at, "symbol": o.symbol, "side": o.side, "qty": o.qty,
                 "order_type": o.order_type, "product": o.product, "price": o.price, "trigger_price": o.trigger_price,
                 "status": o.status, "filled_qty": o.filled_qty, "avg_fill_price": o.avg_price, "costs": o.costs,
                 "filled_at": o.filled_at, "tag": o.tag, "message": o.message} for o in reversed(self.orders)]
        return pd.DataFrame(rows, columns=ORDER_COLUMNS)

    # Positions: open ones (with the latest price) then closed ones.
    def get_positions(self) -> pd.DataFrame:
        live = [self._position_row(p) for p in self.positions.values()]
        if self.save:
            closed = self._db_closed_positions()
        else:
            closed = [self._position_row(p) for p in self.closed]
        return pd.DataFrame(live + closed, columns=POSITION_COLUMNS)

    # One row of the positions table.
    def _position_row(self, p: PaperPosition) -> dict:
        return {"id": p.id, "symbol": p.symbol, "product": p.product, "qty": p.qty, "avg_price": p.avg_price,
                "ltp": p.ltp, "realized_pnl": p.realized, "unrealized_pnl": p.unrealized, "costs": p.costs,
                "net_pnl": to_paise(p.realized + p.unrealized - p.costs), "is_open": p.is_open,
                "opened_at": p.opened_at, "closed_at": p.closed_at}

    # Capital, P&L, costs, money used by open positions and what is left.
    def get_funds(self) -> dict:
        open_ = list(self.positions.values())
        realized = self.closed_realized + sum(p.realized for p in open_ + self.closed)
        costs = self.closed_costs + sum(p.costs for p in open_ + self.closed)
        unrealized = sum(p.unrealized for p in open_)
        used = sum(abs(p.qty) * p.avg_price for p in open_)
        return {"capital": self.capital, "realized_pnl": to_paise(realized), "unrealized_pnl": to_paise(unrealized),
                "costs": to_paise(costs), "net_pnl": to_paise(realized + unrealized - costs),
                "used": to_paise(used), "available": to_paise(self.capital + realized - costs - used)}

    # ---- database ----

    # Load open positions, pending orders and the totals of closed positions (mode = paper).
    def _load(self):
        from sqlalchemy import func, select

        from marketlens.db import get_session
        from marketlens.models import Order, Position
        with get_session(self.url) as session:
            for row in session.scalars(select(Position).where(Position.mode == "paper", Position.is_open)):
                pos = PaperPosition(row.id, row.symbol, row.product, row.segment or segment_for(row.symbol or "", row.product),
                                    row.security_id, row.qty, float(row.avg_price), float(row.realized_pnl),
                                    float(row.costs), float(row.ltp) if row.ltp is not None else None, row.opened_at)
                self.positions[(pos.symbol, pos.product)] = pos
                if pos.ltp is not None:
                    self.last.setdefault(pos.symbol, pos.ltp)
            done = session.execute(select(func.coalesce(func.sum(Position.realized_pnl), 0),
                                          func.coalesce(func.sum(Position.costs), 0))
                                   .where(Position.mode == "paper", Position.is_open.is_(False))).one()
            self.closed_realized, self.closed_costs = float(done[0]), float(done[1])
            for row in session.scalars(select(Order).where(Order.mode == "paper", Order.status.in_(OPEN_STATUSES))
                                       .order_by(Order.id)):
                self.orders.append(PaperOrder(row.id, row.symbol, row.side, row.qty, row.order_type, row.product,
                                              row.segment or segment_for(row.symbol or "", row.product),
                                              float(row.price) if row.price is not None else None,
                                              float(row.trigger_price) if row.trigger_price is not None else None,
                                              row.security_id, row.tag, row.status, placed_at=row.placed_at))

    # Insert or update one order row (or give it a local number when not saving).
    def _save_order(self, o: PaperOrder):
        if not self.save:
            if o.id is None:
                o.id, self._next_id = self._next_id, self._next_id + 1
            return
        from marketlens.db import get_session
        from marketlens.models import Order
        with get_session(self.url) as session:
            row = session.get(Order, o.id) if o.id else None
            if row is None:
                row = Order(mode="paper")
                session.add(row)
            row.symbol, row.security_id, row.segment, row.tag = o.symbol, o.security_id, o.segment, o.tag
            row.side, row.qty, row.order_type, row.product = o.side, o.qty, o.order_type, o.product
            row.price, row.trigger_price, row.status = o.price, o.trigger_price, o.status
            row.filled_qty, row.avg_fill_price, row.costs = o.filled_qty, o.avg_price, o.costs or None
            row.filled_at, row.error_message = o.filled_at, o.message or None
            session.commit()
            o.id = row.id

    # Insert or update one position row.
    def _save_position(self, p: PaperPosition):
        if not self.save:
            if p.id is None:
                p.id, self._next_id = self._next_id, self._next_id + 1
            return
        from marketlens.db import get_session
        from marketlens.models import Position
        with get_session(self.url) as session:
            row = session.get(Position, p.id) if p.id else None
            if row is None:
                row = Position(mode="paper")
                session.add(row)
            row.symbol, row.security_id, row.segment, row.product = p.symbol, p.security_id, p.segment, p.product
            row.qty, row.avg_price, row.ltp = p.qty, p.avg_price, p.ltp
            row.realized_pnl, row.unrealized_pnl, row.costs = p.realized, p.unrealized, p.costs
            row.is_open, row.closed_at = p.is_open, p.closed_at
            session.commit()
            p.id = row.id

    # All paper orders from the database, newest first.
    def _db_orders(self) -> pd.DataFrame:
        from sqlalchemy import select

        from marketlens.db import get_session
        from marketlens.models import Order
        with get_session(self.url) as session:
            rows = session.scalars(select(Order).where(Order.mode == "paper").order_by(Order.id.desc())).all()
            data = [{"id": r.id, "placed_at": r.placed_at, "symbol": r.symbol, "side": r.side, "qty": r.qty,
                     "order_type": r.order_type, "product": r.product, "price": _f(r.price),
                     "trigger_price": _f(r.trigger_price), "status": r.status, "filled_qty": r.filled_qty,
                     "avg_fill_price": _f(r.avg_fill_price), "costs": _f(r.costs), "filled_at": r.filled_at,
                     "tag": r.tag, "message": r.error_message} for r in rows]
        return pd.DataFrame(data, columns=ORDER_COLUMNS)

    # Closed paper positions from the database, newest first.
    def _db_closed_positions(self) -> list[dict]:
        from sqlalchemy import select

        from marketlens.db import get_session
        from marketlens.models import Position
        with get_session(self.url) as session:
            rows = session.scalars(select(Position).where(Position.mode == "paper", Position.is_open.is_(False))
                                   .order_by(Position.id.desc())).all()
            return [{"id": r.id, "symbol": r.symbol, "product": r.product, "qty": r.qty, "avg_price": _f(r.avg_price),
                     "ltp": _f(r.ltp), "realized_pnl": _f(r.realized_pnl), "unrealized_pnl": 0.0, "costs": _f(r.costs),
                     "net_pnl": to_paise(_f(r.realized_pnl) - _f(r.costs)), "is_open": False,
                     "opened_at": r.opened_at, "closed_at": r.closed_at} for r in rows]


# A database number as a float (None stays None).
def _f(value):
    return None if value is None else float(value)
