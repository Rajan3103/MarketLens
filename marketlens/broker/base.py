"""The Broker interface: every broker (paper now, Dhan live later) offers the same five methods.

    broker.place_order("RELIANCE", "BUY", 10)                         # market order
    broker.place_order("NIFTY 25000 CE 03-Nov-2026", "SELL", 65, "LIMIT", price=120.5)
    broker.cancel_order(order_id)
    broker.get_orders()      # table of orders
    broker.get_positions()   # table of positions (qty below 0 = short)
    broker.get_funds()       # capital, P&L, costs, money used and available

Strategies and pages only talk to this interface, so switching paper -> live never changes their code.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

SIDES = ("BUY", "SELL")
ORDER_TYPES = ("MARKET", "LIMIT", "SL", "SL-M")      # SL = stop-limit, SL-M = stop-market
PRODUCTS = ("INTRADAY", "MARGIN", "CNC")             # CNC = delivery (stocks you keep)
OPEN_STATUSES = ("PENDING", "PLACED", "PARTIAL")     # orders that can still fill or be cancelled


@dataclass
class OrderResult:
    """What place_order / cancel_order answer."""
    order_id: int | str | None
    status: str                       # PENDING, PLACED, FILLED, REJECTED, CANCELLED
    filled_qty: int = 0
    avg_price: float | None = None
    costs: float = 0.0
    message: str = ""

    # True when the order is completely filled.
    @property
    def filled(self) -> bool:
        return self.status == "FILLED"


# Check an order's inputs and tidy them (upper-case words); raises ValueError with a simple reason.
def check_order(side: str, qty: int, order_type: str, price: float | None, product: str,
                trigger_price: float | None = None) -> tuple[str, str, str]:
    side, order_type, product = side.upper(), order_type.upper(), product.upper()
    if side not in SIDES:
        raise ValueError(f"side must be BUY or SELL, not {side!r}")
    if not isinstance(qty, (int,)) or isinstance(qty, bool) or qty <= 0:
        raise ValueError("qty must be a whole number above 0")
    if order_type not in ORDER_TYPES:
        raise ValueError(f"order_type must be one of {ORDER_TYPES}")
    if product not in PRODUCTS:
        raise ValueError(f"product must be one of {PRODUCTS}")
    if order_type in ("LIMIT", "SL") and (price is None or price <= 0):
        raise ValueError(f"A {order_type} order needs a price above 0")
    if order_type in ("SL", "SL-M") and (trigger_price is None or trigger_price <= 0):
        raise ValueError(f"A {order_type} order needs a trigger price above 0")
    return side, order_type, product


class Broker(ABC):
    """The five things every broker can do. `mode` is "paper" or "live"."""
    mode: str = "paper"

    # Send an order; symbol is a readable name, security_id Dhan's number for it (when known).
    @abstractmethod
    def place_order(self, symbol: str, side: str, qty: int, order_type: str = "MARKET",
                    price: float | None = None, product: str = "INTRADAY", tag: str | None = None,
                    security_id: str | None = None, trigger_price: float | None = None) -> OrderResult:
        """qty is in units (lots x lot size for F&O)."""

    # Cancel an order that has not filled yet.
    @abstractmethod
    def cancel_order(self, order_id) -> OrderResult:
        """Filled or already cancelled orders cannot be cancelled."""

    # Every order of this mode, newest first.
    @abstractmethod
    def get_orders(self) -> pd.DataFrame:
        """Columns include id, symbol, side, qty, order_type, price, status, avg_fill_price, costs, tag."""

    # Every position (open first), qty below 0 = short.
    @abstractmethod
    def get_positions(self) -> pd.DataFrame:
        """Columns include symbol, product, qty, avg_price, ltp, realized_pnl, unrealized_pnl, costs."""

    # Money: capital, realised / unrealised P&L, costs, used and available.
    @abstractmethod
    def get_funds(self) -> dict:
        """Amounts in rupees."""
