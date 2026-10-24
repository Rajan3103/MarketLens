"""Trading costs for one order: brokerage, taxes and slippage (NSE).

Every backtester, simulator and paper broker calls trade_costs(), so results
include real-world costs. Each page lets you change the CostSettings.

    from marketlens.costs import CostSettings, trade_costs
    c = trade_costs("options", "SELL", price=120.5, qty=75, settings=CostSettings())
    c.total  -> rupees this order costs

`price` is the price per unit (for options: the premium), `qty` is the total
number of units (lots x lot size, e.g. 1 lot of NIFTY = 75).

RATES - last checked 24-Oct-2026. Rates change (STT changed on 1-Apr-2026),
so check them now and then at:
  - Dhan charges:        https://dhan.co/pricing/
  - NSE transaction charges (circulars): https://www.nseindia.com/regulations/exchange-communication-circulars
  - STT (Income Tax Dept / Budget):     https://incometaxindia.gov.in
  - A clear summary of all charges:      https://zerodha.com/charges/
All rates below are in PERCENT of turnover (price x qty) unless the name says _RS.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

SEGMENTS = ("equity_delivery", "equity_intraday", "options", "futures")
SIDES = ("BUY", "SELL")

# ---- Brokerage (Dhan defaults; change them on each page through CostSettings) ----
BROKERAGE_PER_ORDER_RS = 20.0      # F&O and intraday: Rs 20 per executed order
BROKERAGE_CAP_PCT = 0.03           # intraday & futures: Rs 20 or 0.03%, whichever is LOWER
DELIVERY_BROKERAGE_RS = 0.0        # equity delivery: Rs 0 at Dhan

# ---- STT: Securities Transaction Tax (government) ----
STT_DELIVERY_PCT = 0.1             # delivery: 0.1% on BUY and on SELL
STT_INTRADAY_SELL_PCT = 0.025      # intraday: 0.025% on SELL only
STT_FUTURES_SELL_PCT = 0.05        # futures: 0.05% on SELL only (was 0.02% before 1-Apr-2026)
STT_OPTIONS_SELL_PCT = 0.15        # options: 0.15% of PREMIUM on SELL only (was 0.10% before 1-Apr-2026)
# (Options held to expiry and exercised pay 0.15% of intrinsic value instead; not included here.)

# ---- NSE exchange transaction charges (includes the small IPFT fee) ----
EXCHANGE_EQUITY_PCT = 0.00307      # delivery and intraday
EXCHANGE_FUTURES_PCT = 0.00183
EXCHANGE_OPTIONS_PCT = 0.03553     # on premium

# ---- SEBI turnover fee: Rs 10 per crore ----
SEBI_FEE_PCT = 0.0001

# ---- Stamp duty (state government), BUY side only ----
STAMP_DELIVERY_BUY_PCT = 0.015
STAMP_INTRADAY_BUY_PCT = 0.003
STAMP_FUTURES_BUY_PCT = 0.002
STAMP_OPTIONS_BUY_PCT = 0.003

# ---- GST: 18% on (brokerage + exchange charges + SEBI fee) ----
GST_PCT = 18.0

# The same rates arranged by segment, so the code below stays short
STT_PCT = {  # (on BUY, on SELL)
    "equity_delivery": (STT_DELIVERY_PCT, STT_DELIVERY_PCT),
    "equity_intraday": (0.0, STT_INTRADAY_SELL_PCT),
    "futures": (0.0, STT_FUTURES_SELL_PCT),
    "options": (0.0, STT_OPTIONS_SELL_PCT),
}
EXCHANGE_PCT = {
    "equity_delivery": EXCHANGE_EQUITY_PCT,
    "equity_intraday": EXCHANGE_EQUITY_PCT,
    "futures": EXCHANGE_FUTURES_PCT,
    "options": EXCHANGE_OPTIONS_PCT,
}
STAMP_BUY_PCT = {
    "equity_delivery": STAMP_DELIVERY_BUY_PCT,
    "equity_intraday": STAMP_INTRADAY_BUY_PCT,
    "futures": STAMP_FUTURES_BUY_PCT,
    "options": STAMP_OPTIONS_BUY_PCT,
}


@dataclass
class CostSettings:
    """The cost choices a page lets you change.

    slippage_type "pct":    you get a worse price by slippage_value % (e.g. 0.05)
    slippage_type "points": you get a worse price by slippage_value rupees (e.g. 0.5)
    include_taxes False:    only brokerage and slippage (to see results before taxes)
    """
    brokerage_per_order: float = BROKERAGE_PER_ORDER_RS
    brokerage_cap_pct: float | None = BROKERAGE_CAP_PCT   # None = always the flat amount
    delivery_brokerage: float = DELIVERY_BROKERAGE_RS
    slippage_type: str = "pct"
    slippage_value: float = 0.0
    include_taxes: bool = True

    # Check the numbers as soon as settings are made.
    def __post_init__(self):
        """Refuse negative numbers and unknown slippage types."""
        if self.slippage_type not in ("pct", "points"):
            raise ValueError("slippage_type must be 'pct' or 'points'")
        numbers = [self.brokerage_per_order, self.delivery_brokerage, self.slippage_value]
        if self.brokerage_cap_pct is not None:
            numbers.append(self.brokerage_cap_pct)
        if any(n < 0 for n in numbers):
            raise ValueError("Costs and slippage cannot be negative")


@dataclass
class TradeCosts:
    """Every cost of one order, in rupees (rounded to paise)."""
    turnover: float
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp: float
    gst: float
    slippage: float

    # Brokerage plus all taxes and fees (without slippage).
    @property
    def charges(self) -> float:
        """What would appear on the broker's contract note."""
        return to_paise(self.brokerage + self.stt + self.exchange + self.sebi
                        + self.stamp + self.gst)

    # Everything this order costs you, including slippage.
    @property
    def total(self) -> float:
        """charges + slippage."""
        return to_paise(self.charges + self.slippage)


# Round rupees to 2 decimals the way people expect (0.005 -> 0.01).
def to_paise(amount: float) -> float:
    """Avoids float surprises like round(0.015, 2) giving 0.01."""
    return float(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# Work out the rupees lost to slippage on one order.
def slippage_cost(price: float, qty: int, settings: CostSettings) -> float:
    """pct: price x value% per unit; points: value rupees per unit."""
    if settings.slippage_type == "pct":
        per_unit = price * settings.slippage_value / 100
    else:
        per_unit = settings.slippage_value
    return to_paise(per_unit * qty)


# Work out brokerage for one order.
def brokerage(segment: str, turnover: float, settings: CostSettings) -> float:
    """Delivery: delivery_brokerage. Options: flat per order.
    Intraday and futures: flat per order or cap %, whichever is lower."""
    if segment == "equity_delivery":
        return to_paise(settings.delivery_brokerage)
    if segment == "options" or settings.brokerage_cap_pct is None:
        return to_paise(settings.brokerage_per_order)
    return to_paise(min(settings.brokerage_per_order, turnover * settings.brokerage_cap_pct / 100))


# All costs of one BUY or SELL order.
def trade_costs(segment: str, side: str, price: float, qty: int,
                settings: CostSettings | None = None) -> TradeCosts:
    """segment: equity_delivery, equity_intraday, options or futures
    side:    BUY or SELL
    price:   price per unit (premium for options); qty: total units
    Taxes are worked out on price x qty (the slippage is shown separately).
    """
    settings = settings or CostSettings()
    side = side.upper()
    if segment not in SEGMENTS:
        raise ValueError(f"segment must be one of {SEGMENTS}, not {segment!r}")
    if side not in SIDES:
        raise ValueError(f"side must be BUY or SELL, not {side!r}")
    if price <= 0 or qty <= 0:
        raise ValueError("price and qty must be above 0")

    turnover = price * qty
    brok = brokerage(segment, turnover, settings)
    slip = slippage_cost(price, qty, settings)
    if not settings.include_taxes:
        return TradeCosts(to_paise(turnover), brok, 0.0, 0.0, 0.0, 0.0, 0.0, slip)

    buy_rate, sell_rate = STT_PCT[segment]
    stt = turnover * (buy_rate if side == "BUY" else sell_rate) / 100
    exchange = turnover * EXCHANGE_PCT[segment] / 100
    sebi = turnover * SEBI_FEE_PCT / 100
    stamp = turnover * STAMP_BUY_PCT[segment] / 100 if side == "BUY" else 0.0
    gst = (brok + exchange + sebi) * GST_PCT / 100
    return TradeCosts(to_paise(turnover), brok, to_paise(stt), to_paise(exchange),
                      to_paise(sebi), to_paise(stamp), to_paise(gst), slip)


# Costs of a complete trade: open it and close it again.
def round_trip_costs(segment: str, entry_price: float, exit_price: float, qty: int,
                     settings: CostSettings | None = None, entry_side: str = "BUY") -> float:
    """entry_side BUY = buy first then sell (long); SELL = sell first then buy back (short)."""
    exit_side = "SELL" if entry_side.upper() == "BUY" else "BUY"
    opening = trade_costs(segment, entry_side, entry_price, qty, settings)
    closing = trade_costs(segment, exit_side, exit_price, qty, settings)
    return to_paise(opening.total + closing.total)
