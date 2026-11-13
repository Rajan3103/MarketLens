"""Live broker: real orders on your Dhan account through the official dhanhq library.

    broker = DhanBroker()                                     # only works with TRADING_MODE=live + Dhan keys
    broker.place_order("RELIANCE", "BUY", 10, product="CNC")
    broker.place_order("NIFTY 25000 CE 03-Nov-2026", "SELL", 130, "LIMIT", price=120.5)
    broker.modify_order(order_id, price=118)
    broker.cancel_order(order_id)
    broker.place_super_order("NIFTY 25000 CE 03-Nov-2026", "BUY", 65, price=120, target=150, stop_loss=100)
    broker.get_orders(); broker.get_trades(); broker.get_positions(); broker.get_holdings(); broker.get_funds()
    broker.exit_all()                                          # close every position, cancel pending orders

Safety rules (tested in tests/test_dhan_broker.py):
- EVERY method first checks that the Dhan keys are set AND TRADING_MODE=live; otherwise it raises
  LiveTradingDisabled and nothing is sent. Paper trading stays the default.
- Quantities are rounded DOWN to whole lots (130 NIFTY options = 2 lots of 65; 50 = refused, never rounded up).
- At most MAX_ORDERS_PER_SECOND orders are sent per second (Dhan's limit is also enforced by Dhan).
- The access token never appears in a log line or an error message (scrub()).
- Symbols are mapped to Dhan's Security ID with the instrument master (dhan_instruments.py).
- Dhan only accepts orders from your whitelisted static IP (see docs/GUIDE.md, AWS step).
"""
import datetime as dt
import re

import pandas as pd

from marketlens import config
from marketlens.broker import dhan_instruments as di
from marketlens.broker.base import Broker, OrderResult, check_order
from marketlens.broker.dhan_client import DhanError, DhanTemporaryError, get_dhan, has_keys
from marketlens.logger import get_logger
from marketlens.utils import RateLimiter

log = get_logger(__name__)

ORDER_TYPES = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "STOP_LOSS", "SL-M": "STOP_LOSS_MARKET"}
PRODUCTS = {"INTRADAY": "INTRADAY", "MARGIN": "MARGIN", "CNC": "CNC"}
# Dhan's order status -> ours (base.py)
STATUSES = {"TRANSIT": "PLACED", "PENDING": "PLACED", "TRIGGERED": "PLACED", "PART_TRADED": "PARTIAL",
            "TRADED": "FILLED", "CLOSED": "FILLED", "REJECTED": "REJECTED", "CANCELLED": "CANCELLED",
            "EXPIRED": "CANCELLED"}
SUPER_LEGS = ("ENTRY_LEG", "TARGET_LEG", "STOP_LOSS_LEG")
ORDER_COLUMNS = ["id", "placed_at", "symbol", "security_id", "side", "qty", "order_type", "product", "price",
                 "trigger_price", "status", "dhan_status", "filled_qty", "avg_fill_price", "tag", "message"]
OPTION_NAME = re.compile(r"^([A-Z&-]+)\s+(\d+(?:\.\d+)?)\s+(CE|PE)\s+(\d{1,2}-[A-Za-z]{3}-\d{4})$")


class LiveTradingDisabled(RuntimeError):
    """Raised before anything is sent when live trading is not switched on."""


# Stop unless the Dhan keys are set and TRADING_MODE=live (checked by every DhanBroker method).
def require_live():
    if not has_keys():
        raise LiveTradingDisabled("Dhan key not set (DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN): no order was sent. "
                                  "Add them to .env to trade live.")
    if config.TRADING_MODE != "live":
        raise LiveTradingDisabled(f"TRADING_MODE is '{config.TRADING_MODE}', not 'live': no order was sent. "
                                  "Use the paper broker, or set TRADING_MODE=live in .env when you really "
                                  "mean to trade with real money.")


# Remove secrets (the access token, API secret) from any text before it is logged or shown.
def scrub(text) -> str:
    text = str(text)
    for secret in (config.DHAN_ACCESS_TOKEN, config.DHAN_API_SECRET, config.DHAN_API_KEY):
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
    return re.sub(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}", "***", text)      # any JWT-looking token


# Round a quantity DOWN to whole lots; refuse less than one lot.
def round_to_lot(qty: int, lot: int) -> int:
    lot = max(int(lot or 1), 1)
    lots = int(qty) // lot
    if lots < 1:
        raise ValueError(f"Quantity {qty} is less than one lot ({lot}): no order was sent")
    return lots * lot


# The Dhan Security for a readable name: RELIANCE, NIFTY-Nov2026-FUT, NIFTY-Nov2026-25000-CE or
# "NIFTY 25000 CE 03-Nov-2026" (the name the backtester and paper broker use).
def security_for(symbol: str, security_id: str | None = None, master: pd.DataFrame | None = None) -> di.Security:
    master = di.load_master() if master is None else master
    name = symbol.strip().upper()
    sec = None
    match = OPTION_NAME.match(symbol.strip())
    if match:
        underlying, strike, option_type, expiry = match.groups()
        sec = di.find_option(underlying.upper(), dt.datetime.strptime(expiry, "%d-%b-%Y").date(), float(strike),
                             option_type.upper(), master)
    elif name not in di.INDICES:
        sec = di.resolve(name, master)
    if sec is None and security_id:
        rows = master[master["security_id"] == str(security_id)]
        if not rows.empty:
            row = rows.iloc[0]
            segment = "NSE_EQ" if row["segment_code"] == "E" else f"{row['exchange']}_FNO"
            sec = di.Security(row["symbol"], row["security_id"], segment, row["instrument"], row["exchange"],
                              "stock" if segment.endswith("_EQ") else "option", row["display_name"],
                              row["expiry"], int(row["lot_size"]))
    if name in di.INDICES:
        raise ValueError(f"{name} is an index: it cannot be bought or sold. Trade its futures or options.")
    if sec is None:
        raise ValueError(f"{symbol!r} is not in Dhan's instrument list: no order was sent")
    if security_id and str(security_id) != str(sec.security_id):
        raise ValueError(f"Security ID {security_id} does not match {symbol} ({sec.security_id}): no order was sent")
    return sec


class DhanBroker(Broker):
    """Real orders on Dhan. Same methods as PaperBroker (base.py), plus modify, super orders, trade book,
    holdings and exit-all. `dhan` is the dhanhq client (tests pass a fake)."""
    mode = "live"

    # Check the safety rules, then connect (nothing is sent yet).
    def __init__(self, dhan=None, master: pd.DataFrame | None = None, save: bool = True, url: str | None = None,
                 strategy_id: int | None = None):
        require_live()
        self.dhan = dhan if dhan is not None else get_dhan()
        self._master, self.save, self.url, self.strategy_id = master, save, url, strategy_id
        self.limiter = RateLimiter(config.MAX_ORDERS_PER_SECOND)

    # The instrument master (loaded once).
    @property
    def master(self) -> pd.DataFrame:
        if self._master is None:
            self._master = di.load_master()
        return self._master

    # Call dhanhq and return its data; errors are raised with secrets removed.
    def _call(self, method: str, *args, **kwargs):
        require_live()
        try:
            response = getattr(self.dhan, method)(*args, **kwargs)
        except Exception as err:
            raise DhanTemporaryError(f"Dhan call {method} failed: {scrub(err)}") from None
        if not isinstance(response, dict):
            return response
        if response.get("status") == "success":
            return response.get("data")
        remarks = response.get("remarks")
        detail = remarks.get("error_message") or remarks.get("error_type") if isinstance(remarks, dict) else remarks
        code = remarks.get("error_code", "") if isinstance(remarks, dict) else ""
        message = scrub(f"Dhan refused {method}: {code} {detail}".strip())
        if code in ("DH-901", "DH-902"):
            message += " (the access token is not valid: run `python -m marketlens.broker.session renew`)"
        raise DhanError(message)

    # ---------------------------------------------------------------- orders

    # Send an order; qty is in units and is rounded down to whole lots for F&O.
    def place_order(self, symbol: str, side: str, qty: int, order_type: str = "MARKET",
                    price: float | None = None, product: str = "INTRADAY", tag: str | None = None,
                    security_id: str | None = None, trigger_price: float | None = None) -> OrderResult:
        require_live()
        side, order_type, product = check_order(side, qty, order_type, price, product, trigger_price)
        sec = security_for(symbol, security_id, self.master)
        units = round_to_lot(qty, sec.lot_size)
        if product == "CNC" and sec.kind != "stock":
            raise ValueError("CNC (delivery) is only for shares and ETFs; use INTRADAY or MARGIN for F&O")
        self.limiter.wait()
        data = self._call("place_order", security_id=sec.security_id, exchange_segment=sec.segment,
                          transaction_type=side, quantity=units, order_type=ORDER_TYPES[order_type],
                          product_type=PRODUCTS[product], price=float(price or 0),
                          trigger_price=float(trigger_price or 0), tag=tag) or {}
        order_id = data.get("orderId")
        status = STATUSES.get(str(data.get("orderStatus", "TRANSIT")).upper(), "PLACED")
        note = f"rounded {qty} to {units} (lot {sec.lot_size})" if units != qty else ""
        log.info("LIVE order %s %s %s x%s %s: %s %s", order_id, side, sec.symbol, units, order_type, status, note)
        self._save(order_id, sec, side, units, order_type, product, price, trigger_price, status, tag)
        return OrderResult(order_id, status, message=note)

    # Change a pending order's price, trigger, quantity or type (the rest stays as it is).
    def modify_order(self, order_id, order_type: str | None = None, qty: int | None = None,
                     price: float | None = None, trigger_price: float | None = None,
                     validity: str = "DAY", leg_name: str = "ENTRY_LEG") -> OrderResult:
        require_live()
        current = self._call("get_order_by_id", order_id) or {}
        current = current[0] if isinstance(current, list) and current else current
        order_type = (order_type or {v: k for k, v in ORDER_TYPES.items()}.get(current.get("orderType"), "LIMIT")).upper()
        if order_type not in ORDER_TYPES:
            raise ValueError(f"order_type must be one of {list(ORDER_TYPES)}")
        new_qty = int(qty if qty is not None else current.get("quantity", 0))
        lot = self._lot_of(current)
        new_qty = round_to_lot(new_qty, lot)
        price = float(price if price is not None else current.get("price") or 0)
        trigger = float(trigger_price if trigger_price is not None else current.get("triggerPrice") or 0)
        if order_type in ("LIMIT", "SL") and price <= 0:
            raise ValueError(f"A {order_type} order needs a price above 0")
        if order_type in ("SL", "SL-M") and trigger <= 0:
            raise ValueError(f"A {order_type} order needs a trigger price above 0")
        self.limiter.wait()
        data = self._call("modify_order", order_id, ORDER_TYPES[order_type], leg_name, new_qty, price, trigger,
                          int(current.get("disclosedQuantity") or 0), validity) or {}
        status = STATUSES.get(str(data.get("orderStatus", "PENDING")).upper(), "PLACED")
        log.info("LIVE order %s modified: %s x%s price %s trigger %s", order_id, order_type, new_qty, price, trigger)
        return OrderResult(order_id, status)

    # The lot size of an order from Dhan's order book (1 for shares).
    def _lot_of(self, order: dict) -> int:
        sid = str(order.get("securityId") or "")
        rows = self.master[self.master["security_id"] == sid] if sid else pd.DataFrame()
        return int(rows["lot_size"].iloc[0]) if not rows.empty and order.get("exchangeSegment", "").endswith("FNO") else 1

    # Cancel a pending order.
    def cancel_order(self, order_id) -> OrderResult:
        require_live()
        data = self._call("cancel_order", order_id) or {}
        status = STATUSES.get(str(data.get("orderStatus", "CANCELLED")).upper(), "CANCELLED")
        log.info("LIVE order %s cancel requested: %s", order_id, status)
        return OrderResult(order_id, status)

    # A super order: entry plus target and stop loss (and an optional trailing jump) in one go.
    # Dhan replaced bracket (BO) and cover (CO) orders with super orders.
    def place_super_order(self, symbol: str, side: str, qty: int, price: float, target: float, stop_loss: float,
                          trailing_jump: float = 0.0, order_type: str = "LIMIT", product: str = "INTRADAY",
                          tag: str | None = None, security_id: str | None = None) -> OrderResult:
        require_live()
        side, order_type, product = check_order(side, qty, order_type, price, product)
        if order_type not in ("LIMIT", "MARKET"):
            raise ValueError("A super order's entry must be LIMIT or MARKET")
        entry = float(price)
        good = target > entry > stop_loss if side == "BUY" else target < entry < stop_loss
        if not good:
            raise ValueError("For a BUY: target above and stop loss below the price (the other way round for SELL)")
        sec = security_for(symbol, security_id, self.master)
        units = round_to_lot(qty, sec.lot_size)
        self.limiter.wait()
        data = self._call("place_super_order", sec.security_id, sec.segment, side, units, ORDER_TYPES[order_type],
                          PRODUCTS[product], entry, targetPrice=float(target), stopLossPrice=float(stop_loss),
                          trailingJump=float(trailing_jump), tag=tag) or {}
        order_id = data.get("orderId")
        status = STATUSES.get(str(data.get("orderStatus", "TRANSIT")).upper(), "PLACED")
        log.info("LIVE super order %s %s %s x%s @%s target %s SL %s: %s", order_id, side, sec.symbol, units,
                 entry, target, stop_loss, status)
        self._save(order_id, sec, side, units, order_type, product, price, None, status, tag or "super order")
        return OrderResult(order_id, status)

    # Cancel a super order (the entry leg cancels the whole order).
    def cancel_super_order(self, order_id, leg: str = "ENTRY_LEG") -> OrderResult:
        require_live()
        if leg not in SUPER_LEGS:
            raise ValueError(f"leg must be one of {SUPER_LEGS}")
        data = self._call("cancel_super_order", order_id, leg) or {}
        return OrderResult(order_id, STATUSES.get(str(data.get("orderStatus", "CANCELLED")).upper(), "CANCELLED"))

    # Close every open position and cancel pending orders (Dhan's exit-all).
    def exit_all(self) -> str:
        require_live()
        data = self._call("exit_all_positions")
        log.warning("LIVE exit-all sent to Dhan")
        return scrub(data if isinstance(data, str) else (data or {}).get("message", "Exit-all sent"))

    # ---------------------------------------------------------------- books

    # Today's order book, newest first (our column names; Dhan's status kept as dhan_status).
    def get_orders(self) -> pd.DataFrame:
        require_live()
        rows = self._call("get_order_list") or []
        out = [{"id": r.get("orderId"), "placed_at": r.get("createTime"), "symbol": r.get("tradingSymbol"),
                "security_id": r.get("securityId"), "side": r.get("transactionType"), "qty": r.get("quantity"),
                "order_type": {v: k for k, v in ORDER_TYPES.items()}.get(r.get("orderType"), r.get("orderType")),
                "product": r.get("productType"), "price": r.get("price"), "trigger_price": r.get("triggerPrice"),
                "status": STATUSES.get(str(r.get("orderStatus")).upper(), r.get("orderStatus")),
                "dhan_status": r.get("orderStatus"), "filled_qty": r.get("filledQty"),
                "avg_fill_price": r.get("averageTradedPrice"), "tag": r.get("correlationId"),
                "message": r.get("omsErrorDescription")} for r in rows]
        return pd.DataFrame(out, columns=ORDER_COLUMNS).iloc[::-1].reset_index(drop=True)

    # Today's trades (fills).
    def get_trades(self) -> pd.DataFrame:
        require_live()
        rows = self._call("get_trade_book") or []
        columns = ["order_id", "time", "symbol", "security_id", "side", "qty", "price", "product", "segment"]
        out = [{"order_id": r.get("orderId"), "time": r.get("exchangeTime") or r.get("createTime"),
                "symbol": r.get("tradingSymbol"), "security_id": r.get("securityId"), "side": r.get("transactionType"),
                "qty": r.get("tradedQuantity"), "price": r.get("tradedPrice"), "product": r.get("productType"),
                "segment": r.get("exchangeSegment")} for r in rows]
        return pd.DataFrame(out, columns=columns)

    # Today's positions (qty below 0 = short).
    def get_positions(self) -> pd.DataFrame:
        require_live()
        rows = self._call("get_positions") or []
        columns = ["symbol", "security_id", "product", "qty", "avg_price", "buy_avg", "sell_avg", "realized_pnl",
                   "unrealized_pnl", "segment"]
        out = [{"symbol": r.get("tradingSymbol"), "security_id": r.get("securityId"), "product": r.get("productType"),
                "qty": r.get("netQty"), "avg_price": r.get("costPrice"), "buy_avg": r.get("buyAvg"),
                "sell_avg": r.get("sellAvg"), "realized_pnl": r.get("realizedProfit"),
                "unrealized_pnl": r.get("unrealizedProfit"), "segment": r.get("exchangeSegment")} for r in rows]
        return pd.DataFrame(out, columns=columns)

    # Shares and ETFs in your demat account.
    def get_holdings(self) -> pd.DataFrame:
        require_live()
        return pd.DataFrame(self._call("get_holdings") or [])

    # Money: available, used, withdrawable (Dhan spells one field "availabelBalance").
    def get_funds(self) -> dict:
        require_live()
        data = self._call("get_fund_limits") or {}
        return {"available": data.get("availabelBalance", data.get("availableBalance")),
                "used": data.get("utilizedAmount"), "start_of_day": data.get("sodLimit"),
                "collateral": data.get("collateralAmount"), "withdrawable": data.get("withdrawableBalance"),
                "blocked_payout": data.get("blockedPayoutAmount")}

    # ---------------------------------------------------------------- saving

    # Keep a copy of the live order in the orders table (mode='live'), so module 10's journal sees it.
    def _save(self, order_id, sec: di.Security, side, qty, order_type, product, price, trigger_price, status, tag):
        if not self.save:
            return
        from marketlens.broker.paper import segment_for
        from marketlens.db import get_session
        from marketlens.models import Order
        try:
            with get_session(self.url) as session:
                session.add(Order(mode="live", strategy_id=self.strategy_id, symbol=sec.symbol,
                                  security_id=str(sec.security_id), segment=segment_for(sec.symbol, product),
                                  tag=tag, side=side, order_type=order_type, product=product, qty=qty, price=price,
                                  trigger_price=trigger_price, status=status,
                                  broker_order_id=None if order_id is None else str(order_id)))
                session.commit()
        except Exception as err:                 # the order is at Dhan already: never fail because of the copy
            log.error("Live order %s placed but not saved in the database: %s", order_id, scrub(err))

