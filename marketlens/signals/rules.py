"""Indicator rules without code: "EMA(9) crosses above EMA(21) AND RSI(14) > 50".

A rule is a list of Conditions joined by AND or OR. Each Condition compares two Operands:
an indicator (SMA, EMA, RSI, MACD, Bollinger, Supertrend, VWAP, ATR), a price (close, open,
high, low, volume) or a plain number. The same rules are used by the backtester, the scanner
and the Pine Script export, so all three always agree.

    entry = [Condition(Operand("ema", length=9), "crosses_above", Operand("ema", length=21)),
             Condition(Operand("rsi", length=14), ">", Operand("number", value=50))]
    signals = evaluate(entry, "AND", candles)        # True on the candles where the rule holds
"""
import datetime as dt
from dataclasses import asdict, dataclass, field

import pandas as pd

from marketlens.costs import CostSettings
from marketlens.signals import indicators as ind

# kind -> (label, the settings it uses)
OPERANDS = {
    "close": ("Close", []), "open": ("Open", []), "high": ("High", []), "low": ("Low", []),
    "volume": ("Volume", []),
    "sma": ("SMA", ["length"]), "ema": ("EMA", ["length"]), "rsi": ("RSI", ["length"]),
    "atr": ("ATR", ["length"]), "vwap": ("VWAP", []),
    "supertrend": ("Supertrend", ["length", "mult"]),
    "macd": ("MACD line", ["fast", "slow", "signal"]), "macd_signal": ("MACD signal", ["fast", "slow", "signal"]),
    "macd_hist": ("MACD histogram", ["fast", "slow", "signal"]),
    "bb_upper": ("Bollinger upper", ["length", "mult"]), "bb_mid": ("Bollinger middle", ["length", "mult"]),
    "bb_lower": ("Bollinger lower", ["length", "mult"]),
    "number": ("Number", ["value"]),
}
OPERATORS = {"crosses_above": "crosses above", "crosses_below": "crosses below", ">": ">", "<": "<",
             ">=": "≥", "<=": "≤"}
JOINS = ("AND", "OR")
TIMEFRAMES = {"1 day": 1440, "60 min": 60, "25 min": 25, "15 min": 15, "5 min": 5, "1 min": 1}
# Indicators drawn on the price chart (the others get their own panel)
ON_PRICE = {"close", "open", "high", "low", "sma", "ema", "vwap", "supertrend", "bb_upper", "bb_mid", "bb_lower"}


@dataclass
class Operand:
    """One side of a condition: an indicator, a price or a number."""
    kind: str = "close"
    length: int = 14
    mult: float = 2.0
    fast: int = 12
    slow: int = 26
    signal: int = 9
    value: float = 0.0

    # Check the settings.
    def __post_init__(self):
        self.kind = self.kind.lower()
        if self.kind not in OPERANDS:
            raise ValueError(f"Unknown indicator {self.kind!r}; choose from {list(OPERANDS)}")
        self.length, self.fast, self.slow, self.signal = int(self.length), int(self.fast), int(self.slow), int(self.signal)
        if self.length < 1 or self.fast < 1 or self.slow <= self.fast or self.signal < 1 or self.mult <= 0:
            raise ValueError(f"{self.label()}: lengths must be 1 or more (slow > fast) and the multiplier above 0")

    # The settings this operand really uses (others are ignored).
    def used(self) -> dict:
        return {k: getattr(self, k) for k in OPERANDS[self.kind][1]}

    # Readable name, e.g. "EMA(9)", "MACD line(12,26,9)", "50".
    def label(self) -> str:
        if self.kind == "number":
            return f"{self.value:g}"
        args = ",".join(f"{v:g}" for v in self.used().values())
        return OPERANDS[self.kind][0] + (f"({args})" if args else "")

    # The values for every candle.
    def series(self, candles: pd.DataFrame) -> pd.Series:
        c = candles
        k = self.kind
        if k in ("close", "open", "high", "low", "volume"):
            return c[k].astype(float)
        if k == "number":
            return pd.Series(float(self.value), index=c.index)
        if k == "sma":
            return ind.sma(c["close"], self.length)
        if k == "ema":
            return ind.ema(c["close"], self.length)
        if k == "rsi":
            return ind.rsi(c["close"], self.length)
        if k == "atr":
            return ind.atr(c["high"], c["low"], c["close"], self.length)
        if k == "vwap":
            return ind.vwap(c["high"], c["low"], c["close"], c["volume"])
        if k == "supertrend":
            return ind.supertrend(c["high"], c["low"], c["close"], self.length, self.mult)["supertrend"]
        if k.startswith("macd"):
            col = {"macd": "macd", "macd_signal": "signal", "macd_hist": "hist"}[k]
            return ind.macd(c["close"], self.fast, self.slow, self.signal)[col]
        col = {"bb_upper": "upper", "bb_mid": "mid", "bb_lower": "lower"}[k]
        return ind.bollinger(c["close"], self.length, self.mult)[col]


@dataclass
class Condition:
    """left OPERATOR right, e.g. EMA(9) crosses above EMA(21)."""
    left: Operand
    op: str
    right: Operand

    # Accept plain dicts (from the page or the Strategy Library) and check the operator.
    def __post_init__(self):
        if isinstance(self.left, dict):
            self.left = Operand(**self.left)
        if isinstance(self.right, dict):
            self.right = Operand(**self.right)
        if self.op not in OPERATORS:
            raise ValueError(f"Unknown operator {self.op!r}; choose from {list(OPERATORS)}")

    # Readable text, e.g. "EMA(9) crosses above EMA(21)".
    def label(self) -> str:
        return f"{self.left.label()} {OPERATORS[self.op]} {self.right.label()}"

    # True / False for every candle (False while an indicator is still warming up).
    def evaluate(self, candles: pd.DataFrame) -> pd.Series:
        a, b = self.left.series(candles), self.right.series(candles)
        if self.op == "crosses_above":
            out = (a > b) & (a.shift(1) <= b.shift(1))
        elif self.op == "crosses_below":
            out = (a < b) & (a.shift(1) >= b.shift(1))
        else:
            out = {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[self.op]
        return out.fillna(False).astype(bool)


# Several conditions joined by AND (all true) or OR (any true).
def evaluate(conditions: list[Condition], join: str, candles: pd.DataFrame) -> pd.Series:
    if not conditions:
        return pd.Series(False, index=candles.index)
    results = [c.evaluate(candles) for c in conditions]
    out = results[0]
    for r in results[1:]:
        out = (out & r) if join.upper() == "AND" else (out | r)
    return out


@dataclass
class SignalStrategy:
    """Everything that defines an indicator strategy (module 7).

    side long: enter = BUY, exit = SELL; side short: enter = SELL, exit = BUY.
    Exit: the exit rule, the SL / target (in % of the entry price), or the end of the day for
    intraday timeframes (product INTRADAY); daily strategies hold overnight (product CNC).
    """
    symbol: str
    timeframe: str = "1 day"
    start: dt.date = field(default_factory=lambda: dt.date.today() - dt.timedelta(days=3 * 365))
    end: dt.date = field(default_factory=dt.date.today)
    side: str = "long"
    entry: list[Condition] = field(default_factory=list)
    entry_join: str = "AND"
    exit: list[Condition] = field(default_factory=list)
    exit_join: str = "OR"
    sl_pct: float | None = None
    target_pct: float | None = None
    qty: int = 1
    capital: float = 500_000
    costs: CostSettings = field(default_factory=CostSettings)
    name: str = ""

    # Check the settings and accept dicts for the conditions.
    def __post_init__(self):
        self.symbol = self.symbol.strip().upper()
        self.side, self.entry_join, self.exit_join = self.side.lower(), self.entry_join.upper(), self.exit_join.upper()
        self.entry = [c if isinstance(c, Condition) else Condition(**c) for c in self.entry]
        self.exit = [c if isinstance(c, Condition) else Condition(**c) for c in self.exit]
        if isinstance(self.costs, dict):
            self.costs = CostSettings(**self.costs)
        if not self.symbol:
            raise ValueError("Choose an instrument")
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {list(TIMEFRAMES)}")
        if self.side not in ("long", "short"):
            raise ValueError("side must be long or short")
        if self.entry_join not in JOINS or self.exit_join not in JOINS:
            raise ValueError("join must be AND or OR")
        if not self.entry:
            raise ValueError("Add at least one entry condition")
        if self.end < self.start:
            raise ValueError("End date is before start date")
        if any(v is not None and v <= 0 for v in (self.sl_pct, self.target_pct)):
            raise ValueError("SL and target are percentages above 0 (leave empty for none)")
        if int(self.qty) < 1:
            raise ValueError("qty must be 1 or more")
        self.qty = int(self.qty)
        if not self.name:
            self.name = f"{self.symbol} {self.timeframe}: " + f" {self.entry_join} ".join(c.label() for c in self.entry)

    # Minutes per candle (1440 = daily).
    @property
    def minutes(self) -> int:
        return TIMEFRAMES[self.timeframe]

    # Intraday strategies close every position at the end of the day.
    @property
    def intraday(self) -> bool:
        return self.minutes < 1440

    # Every operand used by the rules (for the chart and the Pine inputs).
    def operands(self) -> list[Operand]:
        seen, out = set(), []
        for c in self.entry + self.exit:
            for o in (c.left, c.right):
                key = (o.kind, tuple(o.used().items()))
                if o.kind not in ("number", "close", "open", "high", "low", "volume") and key not in seen:
                    seen.add(key)
                    out.append(o)
        return out

    # The settings as JSON (Strategy Library).
    def to_params(self) -> dict:
        p = asdict(self)
        p["start"], p["end"] = self.start.isoformat(), self.end.isoformat()
        return {**p, "source": "signals_builder"}

    # Back from JSON.
    @classmethod
    def from_params(cls, params: dict) -> "SignalStrategy":
        p = {k: v for k, v in params.items() if k in cls.__dataclass_fields__}
        p["start"], p["end"] = dt.date.fromisoformat(p["start"]), dt.date.fromisoformat(p["end"])
        return cls(**p)


# Save a signal strategy in the Strategy Library (kind "indicator"; the same name is replaced).
# auto=True: webhook alerts naming this strategy place (paper) orders by themselves.
def save_signal_strategy(name: str, strategy: SignalStrategy, url: str | None = None, auto: bool = False) -> int:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Strategy
    if not name.strip():
        raise ValueError("Give the strategy a name")
    with get_session(url) as session:
        row = session.scalar(select(Strategy).where(Strategy.name == name.strip()))
        if row is None:
            row = Strategy(name=name.strip(), kind="indicator")
            session.add(row)
        row.kind, row.params = "indicator", {**strategy.to_params(), "auto": bool(auto)}
        row.description = " / ".join(c.label() for c in strategy.entry)
        session.commit()
        return row.id


# Signal strategies saved by this page.
def list_signal_strategies(url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Strategy
    with get_session(url) as session:
        rows = session.scalars(select(Strategy).where(Strategy.kind == "indicator").order_by(Strategy.name)).all()
        out = [{"id": r.id, "name": r.name, "params": dict(r.params)} for r in rows
               if (r.params or {}).get("source") == "signals_builder"]
    return pd.DataFrame(out, columns=["id", "name", "params"])
