"""Option strategies: legs, payoff (at expiry or on any date), Greeks, probability of profit,
ready-made templates, an approximate margin and saving to the Strategy Library.

    legs = build_template("iron_condor", options, spot, expiry, lot_size=65, gap=2, wing=2)
    pnl_expiry = payoff(legs, prices)                     # at the first expiry
    pnl_today  = payoff(legs, prices, at=datetime.now())   # Black-Scholes value today
    analyse(legs, spot)                                    # max profit/loss, breakevens, POP, Greeks

Used by the Strategy builder (module 2); the backtester (4), paper trading (6) and the
Strategy Library (10) read strategies saved with save_strategy().
"""
import datetime as dt
import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sqlalchemy import delete, select

from marketlens.broker.candles import IST, MARKET_CLOSE
from marketlens.options.pricing import RISK_FREE_RATE, bs_price, greeks
from marketlens.options.strike_select import atm_offset_strike, offset_of

LEG_TYPES = ("CE", "PE", "FUT")
SIDES = ("BUY", "SELL")
DEFAULT_IV = 14.0                   # % used for "today" values when a leg has no IV
NAKED_MARGIN_PCT = 0.12             # approximate SPAN + exposure for sold options / futures
EXPOSURE_MARGIN_PCT = 0.02          # exposure margin on sold options, charged even when hedged
MARGIN_NOTE = "approximate, check with your broker"


@dataclass
class Leg:
    """One position: buy or sell a call (CE), put (PE) or future (FUT)."""
    side: str                       # BUY or SELL
    option_type: str                # CE, PE or FUT
    strike: float | None            # None for futures
    expiry: dt.date
    lots: int = 1
    entry_price: float = 0.0        # premium (options) or price (futures) per unit
    lot_size: int = 1
    iv: float | None = None         # implied volatility in %, for "today" values

    # Tidy and check the inputs as soon as a leg is made.
    def __post_init__(self):
        self.side, self.option_type = self.side.strip().upper(), self.option_type.strip().upper()
        if self.side not in SIDES:
            raise ValueError("side must be BUY or SELL")
        if self.option_type not in LEG_TYPES:
            raise ValueError("option_type must be CE, PE or FUT")
        if self.option_type != "FUT" and (self.strike is None or self.strike <= 0):
            raise ValueError("options need a strike above 0")
        if int(self.lots) < 1 or int(self.lot_size) < 1:
            raise ValueError("lots and lot_size must be at least 1")
        if isinstance(self.expiry, str):
            self.expiry = dt.date.fromisoformat(self.expiry)
        self.strike = None if self.option_type == "FUT" else float(self.strike)
        self.lots, self.lot_size, self.entry_price = int(self.lots), int(self.lot_size), float(self.entry_price)

    # +1 for bought, -1 for sold.
    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1

    # Number of units (lots x lot size).
    @property
    def qty(self) -> int:
        return self.lots * self.lot_size

    # The leg as a plain dict (for tables and saving).
    def to_dict(self) -> dict:
        return asdict(self)


# ---------------- time ----------------

# 15:30 IST on a date: when options expire.
def expiry_time(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, MARKET_CLOSE, tzinfo=IST)


# Years from a moment until a leg's expiry (0 if it has expired).
def years_left(when: dt.datetime, expiry: dt.date) -> float:
    return max((expiry_time(expiry) - when).total_seconds(), 0) / (365 * 24 * 3600)


# The moment to value the strategy: 'expiry' = the first leg's expiry, or a date / date-time.
def valuation_time(legs: list[Leg], at="expiry") -> dt.datetime:
    if isinstance(at, str):
        if at != "expiry":
            raise ValueError("at must be 'expiry', a date or a date-time")
        return expiry_time(min(leg.expiry for leg in legs))
    if isinstance(at, dt.datetime):
        return at if at.tzinfo else at.replace(tzinfo=IST)
    return expiry_time(at)


# ---------------- payoff ----------------

# Value of one unit of a leg at each price (intrinsic at expiry, Black-Scholes before).
def unit_value(leg: Leg, prices: np.ndarray, when: dt.datetime, r: float = RISK_FREE_RATE) -> np.ndarray:
    if leg.option_type == "FUT":
        return np.asarray(prices, dtype=float)
    T = years_left(when, leg.expiry)
    if T <= 0:
        intrinsic = prices - leg.strike if leg.option_type == "CE" else leg.strike - prices
        return np.maximum(intrinsic, 0)
    sigma = (leg.iv or DEFAULT_IV) / 100
    return np.array([bs_price(p, leg.strike, T, r, sigma, leg.option_type) if p > 0
                     else (0.0 if leg.option_type == "CE" else leg.strike * math.exp(-r * T))
                     for p in prices])


# Profit / loss in rupees of all legs at each underlying price.
def payoff(legs: list[Leg], prices, at="expiry", r: float = RISK_FREE_RATE) -> np.ndarray:
    """at='expiry': at the FIRST expiry (later legs, e.g. in a calendar, are valued with
    Black-Scholes). at=a date or date-time: every leg valued at that moment ('today' line)."""
    prices = np.asarray(prices, dtype=float)
    if not legs:
        return np.zeros(len(prices))
    when = valuation_time(legs, at)
    total = np.zeros(len(prices))
    for leg in legs:
        total += leg.sign * (unit_value(leg, prices, when, r) - leg.entry_price) * leg.qty
    return total


# Expiry prices from spot - pct% to spot + pct%, always including every strike.
def price_range(spot: float, pct: float = 10.0, points: int = 801, legs: list[Leg] | None = None) -> np.ndarray:
    grid = np.linspace(spot * (1 - pct / 100), spot * (1 + pct / 100), points)
    strikes = [leg.strike for leg in legs or [] if leg.strike and grid[0] < leg.strike < grid[-1]]
    return np.unique(np.concatenate([grid, strikes]))


# Prices where profit / loss crosses zero (straight line between the drawn points).
def breakevens(prices: np.ndarray, pnl: np.ndarray) -> list[float]:
    points = []
    for i in range(len(prices) - 1):
        a, b = pnl[i], pnl[i + 1]
        if a == 0:
            points.append(float(prices[i]))
        elif a * b < 0:
            points.append(float(prices[i] - a * (prices[i + 1] - prices[i]) / (b - a)))
    if len(pnl) and pnl[-1] == 0:
        points.append(float(prices[-1]))
    return sorted(set(round(p, 2) for p in points))


# Units that keep gaining (or losing) on an endless rally: bought calls + futures - sold ones.
def upside_units(legs: list[Leg]) -> int:
    return sum(leg.sign * leg.qty for leg in legs if leg.option_type in ("CE", "FUT"))


# Units that keep gaining (or losing) on a crash: shorts in futures count like bought puts.
def downside_units(legs: list[Leg]) -> int:
    return sum(leg.sign * leg.qty * (1 if leg.option_type == "PE" else -1)
               for leg in legs if leg.option_type in ("PE", "FUT"))


# Payoff at expiry checked at 0, every strike and across the drawn range.
def _expiry_extremes(legs: list[Leg], prices: np.ndarray) -> tuple[float, float]:
    corners = [0.0] + [leg.strike for leg in legs if leg.strike] + list(prices)
    values = payoff(legs, np.array(corners))
    return float(values.max()), float(values.min())


# Best possible result at expiry; None = unlimited (e.g. bought calls on a rally).
def max_profit(legs: list[Leg], prices: np.ndarray) -> float | None:
    return None if upside_units(legs) > 0 else _expiry_extremes(legs, prices)[0]


# Worst possible result at expiry (a negative number); None = unlimited (e.g. sold calls).
def max_loss(legs: list[Leg], prices: np.ndarray) -> float | None:
    return None if upside_units(legs) < 0 else _expiry_extremes(legs, prices)[1]


# Delta, gamma, theta (Rs per day) and vega (Rs per 1% IV) of the whole position.
def net_greeks(legs: list[Leg], spot: float, when: dt.datetime | None = None,
               r: float = RISK_FREE_RATE) -> dict:
    """delta in units of the underlying (e.g. +65 = like holding 65 units of NIFTY)."""
    when = when or dt.datetime.now(IST)
    total = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for leg in legs:
        if leg.option_type == "FUT":
            total["delta"] += leg.sign * leg.qty
            continue
        T = years_left(when, leg.expiry)
        if T <= 0:
            continue
        g = greeks(spot, leg.strike, T, r, (leg.iv or DEFAULT_IV) / 100, leg.option_type)
        for key in total:
            total[key] += leg.sign * leg.qty * g[key]
    return total


# Chance (0-1) that the strategy makes money at the first expiry, assuming log-normal prices.
def probability_of_profit(legs: list[Leg], spot: float, iv_pct: float, when: dt.datetime | None = None,
                          r: float = RISK_FREE_RATE) -> float:
    """Price at expiry: ln(S_T) ~ Normal(ln S + (r - IV^2/2) T, IV^2 T). Adds up the chances of
    every expiry price where the payoff is above zero."""
    when = when or dt.datetime.now(IST)
    T = years_left(when, min(leg.expiry for leg in legs))
    if T <= 0:
        return float(payoff(legs, np.array([spot]))[0] > 0)
    sigma = iv_pct / 100
    z = np.linspace(-6, 6, 4001)
    prices = spot * np.exp((r - sigma ** 2 / 2) * T + sigma * math.sqrt(T) * z)
    weights = np.exp(-z ** 2 / 2) / math.sqrt(2 * math.pi) * (z[1] - z[0])
    return float(weights[payoff(legs, prices) > 0].sum())


# Money paid (negative) or received (positive) when opening all legs.
def net_premium(legs: list[Leg]) -> float:
    return float(sum(-leg.sign * leg.entry_price * leg.qty for leg in legs if leg.option_type != "FUT"))


# Everything the builder shows, in one dict.
def analyse(legs: list[Leg], spot: float, iv_pct: float | None = None, pct: float = 10.0,
            now: dt.datetime | None = None) -> dict:
    prices = price_range(spot, pct, legs=legs)
    expiry_pnl = payoff(legs, prices)
    best, worst = max_profit(legs, prices), max_loss(legs, prices)
    reward = best / abs(worst) if best is not None and worst not in (None, 0) and worst < 0 else None
    iv = iv_pct or next((leg.iv for leg in legs if leg.iv), DEFAULT_IV)
    return {"prices": prices, "expiry_pnl": expiry_pnl,
            "today_pnl": payoff(legs, prices, at=now or dt.datetime.now(IST)),
            "breakevens": breakevens(prices, expiry_pnl), "max_profit": best, "max_loss": worst,
            "risk_reward": reward, "pop": probability_of_profit(legs, spot, iv, now),
            "greeks": net_greeks(legs, spot, now), "net_premium": net_premium(legs)}


# ---------------- templates ----------------

TEMPLATES = {
    "custom": "Custom (start empty)",
    "long_straddle": "Long straddle", "short_straddle": "Short straddle",
    "long_strangle": "Long strangle", "short_strangle": "Short strangle",
    "iron_condor": "Iron condor", "iron_butterfly": "Iron butterfly",
    "bull_call_spread": "Bull call spread", "bear_put_spread": "Bear put spread",
    "bull_put_spread": "Bull put spread", "bear_call_spread": "Bear call spread",
    "ratio_spread": "Call ratio spread (1x2)", "calendar_spread": "Calendar spread",
}
# Each template: (side, type, strike offset from ATM in "gap"/"wing" units, lots multiplier, far expiry?)
# offsets: "0" = ATM, "+g"/"-g" = gap strikes away, "+gw"/"-gw" = gap + wing strikes away, "+w"/"-w" = wing
_RECIPES = {
    "long_straddle": [("BUY", "CE", "0", 1), ("BUY", "PE", "0", 1)],
    "short_straddle": [("SELL", "CE", "0", 1), ("SELL", "PE", "0", 1)],
    "long_strangle": [("BUY", "CE", "+g", 1), ("BUY", "PE", "-g", 1)],
    "short_strangle": [("SELL", "CE", "+g", 1), ("SELL", "PE", "-g", 1)],
    "iron_condor": [("SELL", "CE", "+g", 1), ("BUY", "CE", "+gw", 1),
                    ("SELL", "PE", "-g", 1), ("BUY", "PE", "-gw", 1)],
    "iron_butterfly": [("SELL", "CE", "0", 1), ("SELL", "PE", "0", 1),
                       ("BUY", "CE", "+w", 1), ("BUY", "PE", "-w", 1)],
    "bull_call_spread": [("BUY", "CE", "0", 1), ("SELL", "CE", "+w", 1)],
    "bear_put_spread": [("BUY", "PE", "0", 1), ("SELL", "PE", "-w", 1)],
    "bull_put_spread": [("SELL", "PE", "0", 1), ("BUY", "PE", "-w", 1)],
    "bear_call_spread": [("SELL", "CE", "0", 1), ("BUY", "CE", "+w", 1)],
    "ratio_spread": [("BUY", "CE", "0", 1), ("SELL", "CE", "+w", 2)],
}


# Turn an offset code like "+gw" into a number of strikes.
def _steps(code: str, gap: int, wing: int) -> int:
    if code == "0":
        return 0
    sign = 1 if code[0] == "+" else -1
    return sign * {"g": gap, "w": wing, "gw": gap + wing}[code[1:]]


# The price of one option from the long table (0 if missing).
def _price(options: pd.DataFrame, strike: float, option_type: str) -> tuple[float, float | None]:
    row = options[(options["strike"] == strike) & (options["option_type"] == option_type)]
    if row.empty:
        raise ValueError(f"{strike:,.0f} {option_type} is not in the chain")
    price, iv = row.iloc[0]["price"], row.iloc[0].get("iv")
    return (float(price) if pd.notna(price) else 0.0), (float(iv) if iv is not None and pd.notna(iv) else None)


# Fill a template's legs from a chain (long table: strike, option_type, price, iv).
def build_template(name: str, options: pd.DataFrame, spot: float, expiry: dt.date, lot_size: int = 1,
                   lots: int = 1, gap: int = 2, wing: int = 2, far_options: pd.DataFrame | None = None,
                   far_expiry: dt.date | None = None) -> list[Leg]:
    """gap: strikes from ATM for strangles / condors; wing: width of the protective (or spread) leg.
    calendar_spread: sell the near ATM call, buy the same strike in far_expiry (needs far_options)."""
    if name not in TEMPLATES:
        raise ValueError(f"Unknown template {name!r}")
    if name == "custom":
        return []
    strikes = options["strike"].unique()
    if name == "calendar_spread":
        if far_options is None or far_expiry is None:
            raise ValueError("A calendar spread needs the far expiry's chain")
        k = atm_offset_strike(strikes, spot, 0)
        near_price, near_iv = _price(options, k, "CE")
        far_price, far_iv = _price(far_options, k, "CE")
        return [Leg("SELL", "CE", k, expiry, lots, near_price, lot_size, near_iv),
                Leg("BUY", "CE", k, far_expiry, lots, far_price, lot_size, far_iv)]
    legs = []
    for side, kind, code, mult in _RECIPES[name]:
        k = atm_offset_strike(strikes, spot, _steps(code, gap, wing))
        price, iv = _price(options, k, kind)
        legs.append(Leg(side, kind, k, expiry, lots * mult, price, lot_size, iv))
    return legs


# ---------------- margin (approximate) ----------------

# A rough margin when Dhan's calculator is not available: label it "approximate, check with your broker".
def approximate_margin(legs: list[Leg], spot: float) -> float:
    """Hedged (every sold leg covered): the maximum loss + about 2% exposure margin on the
    sold side's contract value. Unhedged: about 12% of the uncovered side's value.
    Premiums paid for bought options are added in both cases."""
    if not legs:
        return 0.0
    paid = sum(leg.entry_price * leg.qty for leg in legs if leg.side == "BUY" and leg.option_type != "FUT")
    short_calls = max(0, -upside_units(legs))      # sold calls / futures not covered by bought ones
    short_puts = max(0, -downside_units(legs))     # sold puts / bought futures not covered
    if short_calls == 0 and short_puts == 0:
        # Every sold leg is covered by a bought one: about the maximum loss + exposure margin
        worst = max_loss(legs, price_range(spot, 30, legs=legs))
        sold = max(sum(l.qty for l in legs if l.side == "SELL" and l.option_type == side) for side in ("CE", "PE"))
        loss = abs(worst) if worst is not None else 0.0
        if sold == 0:                      # only bought options: just the premium paid
            return float(paid)
        return float(loss + EXPOSURE_MARGIN_PCT * spot * sold)
    # SPAN only charges the larger side of a short straddle / strangle
    return float(NAKED_MARGIN_PCT * spot * max(short_calls, short_puts) + paid)


# ---------------- Strategy Library (database) ----------------

# Save a strategy and its legs (replaces one with the same name). Returns the strategy id.
def save_strategy(name: str, legs: list[Leg], underlying: str, spot: float, strikes,
                  template: str = "custom", notes: str = "", url: str | None = None) -> int:
    """Each leg's strike is also stored as a rule like ATM+2, so the backtester can reuse it."""
    from marketlens.db import get_session
    from marketlens.models import Strategy, StrategyLeg
    if not name.strip():
        raise ValueError("Give the strategy a name")
    if not legs:
        raise ValueError("Add at least one leg before saving")
    first_expiry = min(leg.expiry for leg in legs)
    with get_session(url) as session:
        strategy = session.scalar(select(Strategy).where(Strategy.name == name.strip()))
        params = {"underlying": underlying.upper(), "spot_at_save": spot, "template": template}
        if strategy is None:
            strategy = Strategy(name=name.strip(), kind="options", params=params, description=notes or None)
            session.add(strategy)
            session.flush()
        else:
            strategy.params, strategy.description = params, notes or strategy.description
            session.execute(delete(StrategyLeg).where(StrategyLeg.strategy_id == strategy.id))
        for no, leg in enumerate(legs, start=1):
            rule = "FUT" if leg.option_type == "FUT" else f"ATM{offset_of(leg.strike, strikes, spot):+d}".replace("+0", "")
            session.add(StrategyLeg(
                strategy_id=strategy.id, leg_no=no, action=leg.side, instrument_kind=leg.option_type,
                strike_rule=rule, expiry_rule="NEAREST" if leg.expiry == first_expiry else "NEXT",
                lots=leg.lots, params={**leg.to_dict(), "expiry": leg.expiry.isoformat()}))
        session.commit()
        return strategy.id


# Read a saved strategy back as legs (exact strikes and prices from when it was saved).
def load_strategy(name: str, url: str | None = None) -> tuple[dict, list[Leg]]:
    from marketlens.db import get_session
    from marketlens.models import Strategy, StrategyLeg
    with get_session(url) as session:
        strategy = session.scalar(select(Strategy).where(Strategy.name == name))
        if strategy is None:
            raise ValueError(f"No saved strategy called {name!r}")
        rows = session.scalars(select(StrategyLeg).where(StrategyLeg.strategy_id == strategy.id)
                               .order_by(StrategyLeg.leg_no)).all()
        legs = [Leg(**r.params) for r in rows]
        info = {"id": strategy.id, "name": strategy.name, **strategy.params,
                "rules": [f"{r.action} {r.instrument_kind} {r.strike_rule} {r.expiry_rule}" for r in rows]}
    return info, legs


# Margin for the legs: Dhan's margin calculator when the keys are set, else the approximate estimate.
def margin_for(legs: list[Leg], underlying: str, spot: float, client=None) -> dict:
    """Returns {"amount", "source", "note"}. source = "Dhan margin calculator" or "approximate".
    Each leg is looked up in Dhan's instrument list to get its Security ID."""
    from marketlens.broker.dhan_instruments import find_option, index_futures
    estimate = {"amount": approximate_margin(legs, spot), "source": "approximate", "note": MARGIN_NOTE}
    if client is None or getattr(client, "is_demo", True) or not client.is_ready() or not legs:
        return estimate
    try:
        orders = []
        for leg in legs:
            if leg.option_type == "FUT":
                sec = next((f for f in index_futures(underlying) if f.expiry == leg.expiry), None)
            else:
                sec = find_option(underlying, leg.expiry, leg.strike, leg.option_type)
            if sec is None:
                raise ValueError(f"{leg.option_type} {leg.strike} {leg.expiry} not found in Dhan's list")
            orders.append({"security": sec, "side": leg.side, "qty": leg.qty, "price": leg.entry_price,
                           "product": "MARGIN"})
        result = client.get_margin(orders)
        if result.get("total_margin") is None:
            raise ValueError("Dhan did not return a total margin")
        return {"amount": float(result["total_margin"]), "source": "Dhan margin calculator", "note": ""}
    except Exception as err:
        return {**estimate, "note": f"{MARGIN_NOTE} (Dhan margin not available: {err})"}
