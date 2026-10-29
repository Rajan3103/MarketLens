"""Options backtester (module 4): run a multi-leg option strategy on stored minute data.

How one trading day works
  1. Pick the expiry (weekly/monthly, current/next) and load that day's minutes from
     PostgreSQL with load_option_day(), turned into numpy arrays (fast to look up).
  2. Enter at the entry time, or when a price range breaks (range breakout). Each leg's strike
     is chosen then (ATM +/- N, closest premium or closest delta, with strike_select.py).
  3. Every minute: check each leg's stop loss / target on that minute's high and low, move
     trailing stops, re-enter after a stop loss if asked, and check the whole strategy's M2M.
  4. Exit what is still open at the exit time. Each entry of each leg becomes one Trade; costs
     come from costs.py (CostSettings).

NO LOOK-AHEAD: a minute candle stamped 09:19 covers 09:19:00-09:19:59, so its close is only
known at 09:20. So:
  - a DECISION at 09:20 (choosing strikes, entering, the overall M2M check, the time exit) uses
    the close of the 09:19 candle, the last candle that has finished;
  - a stop loss, target, re-entry at cost or leg breakout is an order waiting in the market, so it
    fills DURING a candle, at its level (or at the candle's open if the price jumped past it).
    Whatever that candle did afterwards is only used from the next minute on.

    settings = OptionsBacktestSettings("NIFTY", date(2025, 1, 1), date(2025, 12, 31),
                                       legs=[LegSpec("SELL", "CE", sl=30), LegSpec("SELL", "PE", sl=30)])
    result = run_options_backtest(settings)       # also saved with save_run()
"""
import datetime as dt
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd

from marketlens.backtest.engine import BacktestResult, Trade
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings, apply_slippage, to_paise
from marketlens.logger import get_logger
from marketlens.options import expiries
from marketlens.options.chain import DEFAULT_LOT_SIZES
from marketlens.options.pricing import RISK_FREE_RATE, implied_vol
from marketlens.options.strike_select import atm_offset_strike, strike_for_delta, strike_for_premium

log = get_logger(__name__)

MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)
BARS = 375                 # one-minute candles 09:15 .. 15:29
STALE_MINUTES = 5          # a strike with no candle in the last 5 minutes is not chosen
PREFETCH_DAYS = 4          # days loaded ahead in the background while the current day runs
LOAD_THREADS = 2           # background threads that load days from PostgreSQL
STRIKE_RULES = {"ATM": "ATM ± N", "PREMIUM": "Closest premium (Rs)", "DELTA": "Closest delta"}
UNITS = {"pct": "% of entry price", "points": "price points", "rs": "Rs on the whole leg"}
REENTRY = {"none": "No re-entry", "cost": "Re-enter at cost (when the price comes back to the entry)",
           "reexecute": "Re-execute (new strike, next minute)"}
EXIT_REASONS = {"SL": "Stop loss", "TARGET": "Target", "TRAIL_SL": "Trailing / locked stop loss",
                "COST_SL": "Stop loss moved to cost", "M2M_SL": "Overall M2M stop loss",
                "M2M_TARGET": "Overall M2M target", "M2M_TRAIL": "Overall trailing / locked M2M",
                "TIME": "Exit time"}
BOTH_HIT = "SL and target both touched in this minute: assumed SL (worse case)"


# Check that two optional settings are given together and are above 0.
def _pair(a, b, names: str):
    if (a is None) != (b is None):
        raise ValueError(f"{names}: give both values or neither")
    if a is not None and (a <= 0 or b <= 0):
        raise ValueError(f"{names} must be above 0")


@dataclass
class LegSpec:
    """One leg of the strategy: what to trade, how to pick its strike and how it exits.

    strike_rule ATM: value = N strikes from ATM (+2 = two higher); PREMIUM: value = Rs; DELTA: value = 0.20
    unit:            how sl / target / trailing / lock are measured: pct, points or rs (whole leg)
    sl, target:      stop loss and target distance from the entry price
    trail_x, trail_y: for every trail_x move in your favour, move the SL trail_y in your favour
    lock_at, lock_to: once the move in your favour reaches lock_at, move the SL to lock lock_to profit
    reentry:         after a stop loss: none, cost (re-enter when the price returns to the entry)
                     or reexecute (enter again at the next minute with a newly chosen strike)
    """
    side: str
    option_type: str
    lots: int = 1
    strike_rule: str = "ATM"
    value: float = 0
    unit: str = "pct"
    sl: float | None = None
    target: float | None = None
    trail_x: float | None = None
    trail_y: float | None = None
    lock_at: float | None = None
    lock_to: float | None = None
    reentry: str = "none"
    max_reentries: int = 0

    # Check and tidy the inputs.
    def __post_init__(self):
        self.side, self.option_type = self.side.upper(), self.option_type.upper()
        self.strike_rule, self.unit, self.reentry = self.strike_rule.upper(), self.unit.lower(), self.reentry.lower()
        if self.side not in ("BUY", "SELL") or self.option_type not in ("CE", "PE"):
            raise ValueError("A leg needs side BUY/SELL and option type CE/PE")
        if self.strike_rule not in STRIKE_RULES:
            raise ValueError(f"strike_rule must be one of {list(STRIKE_RULES)}")
        if int(self.lots) < 1:
            raise ValueError("lots must be 1 or more")
        if self.strike_rule in ("PREMIUM", "DELTA") and self.value <= 0:
            raise ValueError(f"{self.strike_rule} needs a value above 0")
        if self.unit not in UNITS:
            raise ValueError(f"unit must be one of {list(UNITS)}")
        if any(v is not None and v <= 0 for v in (self.sl, self.target)):
            raise ValueError("Stop loss and target must be above 0")
        _pair(self.trail_x, self.trail_y, "Trailing (trail_x, trail_y)")
        _pair(self.lock_at, self.lock_to, "Lock profit (lock_at, lock_to)")
        if self.trail_x is not None and self.sl is None:
            raise ValueError("A trailing stop needs a stop loss to move")
        if self.lock_at is not None and self.lock_to >= self.lock_at:
            raise ValueError("lock_to must be smaller than lock_at")
        if self.reentry not in REENTRY:
            raise ValueError(f"reentry must be one of {list(REENTRY)}")
        if self.reentry != "none" and int(self.max_reentries) < 1:
            raise ValueError("Set max_reentries to 1 or more for re-entry")
        self.lots, self.max_reentries = int(self.lots), int(self.max_reentries)

    # Short text like "SELL 1x CE ATM+2 SL 30%".
    def describe(self) -> str:
        if self.strike_rule == "ATM":
            rule = f"ATM{int(self.value):+d}" if self.value else "ATM"
        elif self.strike_rule == "PREMIUM":
            rule = f"premium ~Rs {self.value:g}"
        else:
            rule = f"delta ~{self.value:.2f}"
        unit = {"pct": "%", "points": " pts", "rs": " Rs"}[self.unit]
        extras = [f"SL {self.sl:g}{unit}" if self.sl else "", f"TGT {self.target:g}{unit}" if self.target else ""]
        return " ".join([f"{self.side} {self.lots}x {self.option_type} {rule}", *filter(None, extras)])


@dataclass
class OptionsBacktestSettings:
    """Everything that defines one options backtest.

    entry_mode "time":     enter at entry_time
    entry_mode "breakout": record the range from range_start to range_end, then enter when it breaks
                           (range_source "underlying" = the index for all legs; "leg" = each leg's own price)
    m2m_sl / m2m_target:   whole-strategy stop loss / target in Rs (P&L before costs); exits all legs
    m2m_trail_x / _y:      for every m2m_trail_x Rs of new profit, raise the M2M stop by m2m_trail_y Rs
    m2m_lock_at / _to:     once M2M reaches m2m_lock_at Rs, never give back below m2m_lock_to Rs
    """
    underlying: str
    start: dt.date
    end: dt.date
    legs: list[LegSpec]
    expiry_type: str = "weekly"                 # weekly or monthly
    expiry_which: str = "current"               # current or next
    entry_time: dt.time = dt.time(9, 20)
    exit_time: dt.time = dt.time(15, 15)
    weekdays: tuple = (0, 1, 2, 3, 4)           # 0 = Monday ... 4 = Friday; (1,) = only Tuesdays
    skip_expiry_day: bool = False
    lot_size: int | None = None                 # None = today's lot size for the underlying
    capital: float = 500_000
    costs: CostSettings = field(default_factory=CostSettings)
    name: str = ""
    entry_mode: str = "time"
    range_start: dt.time = dt.time(9, 15)
    range_end: dt.time = dt.time(9, 30)
    range_source: str = "underlying"
    breakout_side: str = "either"               # high, low or either
    move_sl_to_cost: bool = False               # after any leg's SL, move the other legs' SL to their entry
    m2m_sl: float | None = None
    m2m_target: float | None = None
    m2m_trail_x: float | None = None
    m2m_trail_y: float | None = None
    m2m_lock_at: float | None = None
    m2m_lock_to: float | None = None

    # Check the settings and fill in the defaults.
    def __post_init__(self):
        self.underlying = self.underlying.upper()
        self.expiry_type, self.expiry_which = self.expiry_type.lower(), self.expiry_which.lower()
        self.entry_mode, self.range_source = self.entry_mode.lower(), self.range_source.lower()
        self.breakout_side = self.breakout_side.lower()
        self.legs = [leg if isinstance(leg, LegSpec) else LegSpec(**leg) for leg in self.legs]
        if not self.legs:
            raise ValueError("Add at least one leg")
        if self.end < self.start:
            raise ValueError("End date is before start date")
        if self.expiry_type not in ("weekly", "monthly") or self.expiry_which not in ("current", "next"):
            raise ValueError("expiry_type must be weekly/monthly and expiry_which current/next")
        if not (MARKET_OPEN < self.entry_time < self.exit_time <= MARKET_CLOSE):
            raise ValueError("Entry must be after 09:15 and before the exit; exit at 15:30 at the latest")
        if not self.weekdays or any(d not in range(5) for d in self.weekdays):
            raise ValueError("weekdays must be numbers 0 (Monday) to 4 (Friday)")
        if self.entry_mode not in ("time", "breakout"):
            raise ValueError("entry_mode must be time or breakout")
        if self.range_source not in ("underlying", "leg") or self.breakout_side not in ("high", "low", "either"):
            raise ValueError("range_source must be underlying/leg and breakout_side high/low/either")
        if self.entry_mode == "breakout" and not (MARKET_OPEN <= self.range_start < self.range_end < self.exit_time):
            raise ValueError("The range must start at 09:15 or later and end before the exit time")
        if any(v is not None and v <= 0 for v in (self.m2m_sl, self.m2m_target)):
            raise ValueError("M2M stop loss and target must be above 0 (in Rs)")
        _pair(self.m2m_trail_x, self.m2m_trail_y, "M2M trailing (m2m_trail_x, m2m_trail_y)")
        _pair(self.m2m_lock_at, self.m2m_lock_to, "M2M lock (m2m_lock_at, m2m_lock_to)")
        if self.m2m_trail_x is not None and self.m2m_sl is None and self.m2m_lock_at is None:
            raise ValueError("M2M trailing needs an M2M stop loss or a lock to move")
        if self.m2m_lock_at is not None and self.m2m_lock_to >= self.m2m_lock_at:
            raise ValueError("m2m_lock_to must be smaller than m2m_lock_at")
        self.weekdays = tuple(sorted(set(self.weekdays)))
        self.lot_size = int(self.lot_size or DEFAULT_LOT_SIZES.get(self.underlying, 1))
        if not self.name:
            legs = ", ".join(leg.describe() for leg in self.legs)
            when = (f"{self.entry_time:%H:%M}-{self.exit_time:%H:%M}" if self.entry_mode == "time"
                    else f"breakout {self.range_start:%H:%M}-{self.range_end:%H:%M}, exit {self.exit_time:%H:%M}")
            self.name = f"{self.underlying} {legs} {when}"

    # The settings as plain JSON (saved with the run in backtest_runs.params).
    def to_params(self) -> dict:
        params = {k: v for k, v in asdict(self).items() if k not in ("legs", "costs", "name")}
        for k, v in params.items():
            if isinstance(v, dt.time):
                params[k] = f"{v:%H:%M}"
            elif isinstance(v, dt.date):
                params[k] = v.isoformat()
            elif isinstance(v, tuple):
                params[k] = list(v)
        return {**params, "legs": [asdict(leg) for leg in self.legs], "costs": asdict(self.costs)}


# ---------------------------------------------------------------- data

class StoredOptionData:
    """Reads option minutes from PostgreSQL (what download_expired_options stored)."""

    def __init__(self, underlying: str, url: str | None = None):
        self.underlying, self.url = underlying.upper(), url

    # Days that have stored option minutes.
    def trading_days(self, start: dt.date, end: dt.date) -> list[dt.date]:
        from marketlens.ingest.options import stored_days
        return stored_days(self.underlying, start, end, self.url)

    # One day of one expiry (the same table as load_option_day).
    def load_day(self, day: dt.date, expiry: dt.date) -> pd.DataFrame:
        from marketlens.ingest.options import load_option_day
        return load_option_day(self.underlying, day, expiry, self.url)


# Minute number in the day: 09:15 -> 0, 09:16 -> 1 ... 15:30 -> 375.
def minute_index(t: dt.time) -> int:
    return (t.hour * 60 + t.minute) - (MARKET_OPEN.hour * 60 + MARKET_OPEN.minute)


# Copy the last real value forward along the minute axis; also return how old it is.
def _forward_fill(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    minutes = np.arange(raw.shape[-1])
    last = np.where(~np.isnan(raw), minutes, -1)
    np.maximum.accumulate(last, axis=-1, out=last)
    filled = np.take_along_axis(raw, np.clip(last, 0, None), axis=-1)
    filled[last < 0] = np.nan
    age = np.where(last < 0, BARS + 1, minutes - last)
    return filled, age


@dataclass
class DayArrays:
    """One day of option minutes as numpy arrays (fast to look up, made once per day).

    price[t, s, m]: close of candle m (0 = 09:15) for type t (0 = CE, 1 = PE) and strike s, carried
                    forward over minutes without a candle; age = minutes since a real candle.
    open / high / low: the candle's values; a minute without a candle uses the carried close.
    """
    day: dt.date
    expiry: dt.date
    strikes: np.ndarray
    price: np.ndarray
    age: np.ndarray
    iv: np.ndarray
    spot: np.ndarray
    is_demo: bool = False
    open: np.ndarray | None = None
    high: np.ndarray | None = None
    low: np.ndarray | None = None
    oi: np.ndarray | None = None               # open interest, carried forward like the price

    # Turn a load_option_day() table into arrays.
    @classmethod
    def from_frame(cls, df: pd.DataFrame, day: dt.date, expiry: dt.date) -> "DayArrays":
        ts = pd.to_datetime(df["ts"])
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert(IST)
        m = (ts.dt.hour * 60 + ts.dt.minute - (9 * 60 + 15)).to_numpy()
        keep = (m >= 0) & (m < BARS)
        df, m = df[keep], m[keep]
        strikes = np.unique(df["strike"].to_numpy(dtype=float))
        t = (df["option_type"].to_numpy() == "PE").astype(int)
        s = np.searchsorted(strikes, df["strike"].to_numpy(dtype=float))

        # One (2, strikes, minutes) array per column, empty (NaN) where there is no candle.
        def grid(column: str) -> np.ndarray:
            out = np.full((2, len(strikes), BARS), np.nan)
            out[t, s, m] = pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)
            return out

        price, age = _forward_fill(grid("close"))
        iv, _ = _forward_fill(grid("iv"))
        raw_spot = np.full(BARS, np.nan)
        raw_spot[m] = pd.to_numeric(df["spot"], errors="coerce").to_numpy(dtype=float)
        spot, _ = _forward_fill(raw_spot)
        ohl = {}
        for column in ("open", "high", "low"):
            raw = grid(column) if column in df else np.full_like(price, np.nan)
            ohl[column] = np.where((age == 0) & ~np.isnan(raw), raw, price)
        oi = _forward_fill(grid("oi"))[0] if "oi" in df else None
        demo = bool(df["is_demo"].any()) if "is_demo" in df else False
        return cls(day, expiry, strikes, price, age, iv, spot, demo, **ohl, oi=oi)

    # Index of the last candle that has FINISHED at time t (09:20 -> the 09:19 candle).
    @staticmethod
    def known_bar(t: dt.time) -> int:
        k = min(minute_index(t), BARS) - 1
        if k < 0:
            raise ValueError("Nothing is known at 09:15: no candle has finished yet")
        return k

    # The options you could see once candle k finished: strike, type, price, IV (recent candles only).
    def options_at_bar(self, k: int) -> pd.DataFrame:
        fresh = self.age[:, :, k] <= STALE_MINUTES
        ti, si = np.nonzero(fresh)
        return pd.DataFrame({"strike": self.strikes[si], "option_type": np.where(ti == 0, "CE", "PE"),
                             "price": self.price[ti, si, k], "iv": self.iv[ti, si, k],
                             "delta": np.nan, "oi": np.nan})

    # The same, for a clock time.
    def options_at(self, t: dt.time) -> pd.DataFrame:
        return self.options_at_bar(self.known_bar(t))

    # The underlying's close for every minute. Without stored index minutes it is worked out from
    # put-call parity at the strike where the call and put prices are closest: strike + call - put.
    def spot_series(self) -> np.ndarray:
        if getattr(self, "_spot_series", None) is not None:
            return self._spot_series
        if not np.isnan(self.spot).any():
            self._spot_series = self.spot
            return self.spot
        ce, pe = self.price[0], self.price[1]
        gap = np.abs(ce - pe)
        gap[np.isnan(gap)] = np.inf
        best = np.argmin(gap, axis=0)
        cols = np.arange(BARS)
        parity = self.strikes[best] + ce[best, cols] - pe[best, cols]
        parity[np.isinf(gap[best, cols])] = np.nan
        self._spot_series = np.where(np.isnan(self.spot), parity, self.spot)     # worked out once per day
        return self._spot_series

    # The underlying price once candle k finished.
    def spot_at_bar(self, k: int) -> float:
        value = self.spot_series()[k]
        if math.isnan(value):
            raise ValueError("No spot price and no strike with both a call and a put price")
        return float(value)

    # The same, for a clock time.
    def spot_at(self, t: dt.time) -> float:
        return self.spot_at_bar(self.known_bar(t))

    # Row number of a strike in the arrays.
    def strike_row(self, strike: float) -> int:
        i = int(np.searchsorted(self.strikes, strike))
        if i >= len(self.strikes) or self.strikes[i] != strike:
            raise ValueError(f"Strike {strike:g} has no data on {self.day}")
        return i


# ---------------------------------------------------------------- one day

# The price you really get: worse than the candle by the slippage (buy higher, sell lower).
def fill_price(price: float, order_side: str, costs: CostSettings) -> float:
    return apply_slippage(price, order_side, costs)


# Years from a moment to 15:30 on the expiry day (for delta).
def years_to_expiry(when: dt.datetime, expiry: dt.date) -> float:
    close = dt.datetime.combine(expiry, MARKET_CLOSE, IST)
    return max((close - when).total_seconds(), 60) / (365 * 24 * 3600)


# Choose the strike of one leg from the options known at that moment.
def pick_strike(leg: LegSpec, options: pd.DataFrame, spot: float, years: float) -> float:
    if leg.strike_rule == "ATM":
        return atm_offset_strike(options.loc[options["option_type"] == leg.option_type, "strike"].unique(),
                                 spot, int(leg.value))
    if leg.strike_rule == "PREMIUM":
        return strike_for_premium(options, leg.option_type, leg.value)
    missing = options["iv"].isna() | (options["iv"] <= 0)
    if missing.any():            # no stored IV: work it out from the price
        options = options.copy()
        options.loc[missing, "iv"] = [implied_vol(p, spot, k, years, RISK_FREE_RATE, t) * 100 for p, k, t in
                                      options.loc[missing, ["price", "strike", "option_type"]].to_numpy()]
    return strike_for_delta(options, leg.option_type, leg.value, spot=spot, years=years)


# Join notes, skipping empty ones.
def _join(*notes) -> str:
    return "; ".join(n for n in notes if n)


@dataclass
class _LegState:
    """What one leg is doing during the day.

    status: scheduled (enter at minute `wake` with a fresh strike), breakout (waiting for its own
    range to break), open, cost (waiting for the price to come back to the entry), done.
    """
    spec: LegSpec
    no: int
    qty: int
    status: str = "scheduled"
    wake: int = 0
    strike: float = 0.0
    prices: tuple = ()                 # (open, high, low, close) lists of the chosen strike
    entry: float = 0.0                 # entry price after slippage
    entry_mark: float = 0.0            # the market price at entry (re-entry at cost waits for it)
    entry_at: dt.datetime | None = None
    monitor_from: int = 0
    factor: float = 1.0                # converts the leg's unit into price points
    sl: float | None = None
    sl0: float | None = None
    target: float | None = None
    sl_reason: str = "SL"
    best: float = 0.0                  # biggest move in our favour so far (points)
    reentries: int = 0
    range_hi: float = 0.0
    range_lo: float = 0.0
    entry_note: str = ""

    # True for a sold leg (profits when the price falls).
    @property
    def short(self) -> bool:
        return self.spec.side == "SELL"


class _DayRun:
    """Runs the strategy minute by minute on one day. Use run_day()."""

    def __init__(self, settings: OptionsBacktestSettings, day: DayArrays):
        self.s, self.d = settings, day
        self.costs, self.no_slip = settings.costs, replace(settings.costs, slippage_value=0.0)
        self.last = day.known_bar(settings.exit_time)          # last candle watched (exit uses its close)
        self.legs = [_LegState(spec, no, spec.lots * settings.lot_size) for no, spec in enumerate(settings.legs, 1)]
        self.trades: list[Trade] = []
        self.curve: list[tuple[dt.time, float]] = []
        self.realised, self.m2m_best, self.m2m_floor_raised = 0.0, -math.inf, False
        self.first_entry: dt.datetime | None = None
        self.spot_entry: float | None = None
        self.cost_moves: list[_LegState] = []

    # The clock time at the start of candle m.
    def at(self, m: int) -> dt.datetime:
        return dt.datetime.combine(self.d.day, MARKET_OPEN, IST) + dt.timedelta(minutes=m)

    # The exit-time clock (the time exit happens at the exit time, not at the last candle's start).
    def exit_at(self) -> dt.datetime:
        return dt.datetime.combine(self.d.day, self.s.exit_time, IST)

    # Pick a strike for a leg with what is known once candle k finished.
    def choose(self, leg: _LegState, k: int) -> float:
        options = self.d.options_at_bar(k)
        spot = self.d.spot_at_bar(k)
        if self.spot_entry is None:
            self.spot_entry = spot
        return pick_strike(leg.spec, options, spot, years_to_expiry(self.at(k + 1), self.d.expiry))

    # Point the leg at a strike: its open/high/low/close lists for the day.
    def use_strike(self, leg: _LegState, strike: float):
        t, s = (0 if leg.spec.option_type == "CE" else 1), self.d.strike_row(strike)
        leg.strike = strike
        leg.prices = tuple(arr[t, s].tolist() for arr in (self.d.open, self.d.high, self.d.low, self.d.price))

    # Enter a leg at `mark` (before slippage) at the start of candle m; watch it from `monitor_from`.
    def enter(self, leg: _LegState, mark: float, m: int, monitor_from: int, note: str = ""):
        if mark is None or math.isnan(mark) or mark <= 0:
            raise ValueError(f"No {leg.spec.option_type} {leg.strike:g} price to enter at")
        sp = leg.spec
        leg.entry_mark, leg.entry = mark, fill_price(mark, sp.side, self.costs)
        leg.entry_at, leg.monitor_from, leg.status, leg.best = self.at(m), monitor_from, "open", 0.0
        leg.entry_note, leg.sl_reason = note, "SL"
        leg.factor = {"pct": leg.entry / 100, "points": 1.0, "rs": 1 / leg.qty}[sp.unit]
        away = 1 if leg.short else -1               # the SL is above a sold leg, below a bought leg
        leg.sl = leg.entry + away * sp.sl * leg.factor if sp.sl else None
        leg.target = leg.entry - away * sp.target * leg.factor if sp.target else None
        leg.sl0 = leg.sl
        if self.first_entry is None:
            self.first_entry = leg.entry_at

    # Close a leg at `mark` (before slippage) at time `when`.
    def close(self, leg: _LegState, mark: float, when: dt.datetime, reason: str, note: str = ""):
        sp = leg.spec
        exit_ = fill_price(mark, "BUY" if leg.short else "SELL", self.costs)
        trade = Trade(f"{self.s.underlying} {leg.strike:g} {sp.option_type} {self.d.expiry:%d-%b-%Y}", sp.side,
                      leg.qty, leg.entry_at, leg.entry, max(when, leg.entry_at), exit_, segment="options",
                      leg_no=leg.no, exit_reason=reason, note=_join(leg.entry_note, note) or None,
                      settings=self.no_slip)
        self.trades.append(trade)
        self.realised += trade.gross_pnl
        leg.status = "done"

    # Stop loss and target on candle m (on its high and low), then trailing for the next minutes.
    def check_leg(self, leg: _LegState, m: int):
        o, h, low = leg.prices[0][m], leg.prices[1][m], leg.prices[2][m]
        short = leg.short
        sl_hit = leg.sl is not None and (h >= leg.sl if short else low <= leg.sl)
        tg_hit = leg.target is not None and (low <= leg.target if short else h >= leg.target)
        if sl_hit:
            gapped = o >= leg.sl if short else o <= leg.sl
            note = _join(BOTH_HIT if tg_hit else "", "price opened past the SL: exited at the open" if gapped else "")
            reason = leg.sl_reason
            self.close(leg, o if gapped else leg.sl, self.at(m), reason, note)
            self.after_stop(leg, m)
            return
        if tg_hit:
            gapped = o <= leg.target if short else o >= leg.target
            self.close(leg, o if gapped else leg.target, self.at(m), "TARGET",
                       "price opened past the target: exited at the open" if gapped else "")
            return
        move = (leg.entry - low) if short else (h - leg.entry)      # best price of this minute
        if move > leg.best:
            leg.best = move
            self.trail(leg)

    # Move the leg's SL in our favour: trailing steps and the lock-profit level.
    def trail(self, leg: _LegState):
        sp, f, short = leg.spec, leg.factor, leg.short
        toward = -1 if short else 1                  # SL moves down for a sold leg, up for a bought leg
        candidates = []
        if sp.trail_x is not None:
            steps = math.floor(leg.best / (sp.trail_x * f) + 1e-9)
            if steps > 0:
                candidates.append(leg.sl0 + toward * steps * sp.trail_y * f)
        if sp.lock_at is not None and leg.best >= sp.lock_at * f - 1e-9:
            candidates.append(leg.entry + toward * sp.lock_to * f)
        for level in candidates:
            if leg.sl is None or (level < leg.sl if short else level > leg.sl):
                leg.sl, leg.sl_reason = level, "TRAIL_SL"

    # After a stop loss: queue "move others to cost" and the leg's re-entry.
    def after_stop(self, leg: _LegState, m: int):
        if self.s.move_sl_to_cost:
            self.cost_moves += [other for other in self.legs if other is not leg and other.status == "open"]
        sp = leg.spec
        if sp.reentry != "none" and leg.reentries < sp.max_reentries and m + 1 <= self.last:
            leg.reentries += 1
            leg.status, leg.wake = ("cost" if sp.reentry == "cost" else "scheduled"), m + 1

    # Move queued legs' SL to their entry price (only when that is tighter than the SL they have).
    def apply_cost_moves(self):
        for leg in self.cost_moves:
            if leg.status != "open":
                continue
            if leg.sl is None or (leg.entry < leg.sl if leg.short else leg.entry > leg.sl):
                leg.sl, leg.sl_reason = leg.entry, "COST_SL"
        self.cost_moves = []

    # A leg waiting to re-enter at cost: enters on candle m if the price comes back to its entry.
    def check_cost(self, leg: _LegState, m: int):
        o, h, low = leg.prices[0][m], leg.prices[1][m], leg.prices[2][m]
        level = leg.entry_mark
        back = low <= level if leg.short else h >= level
        if back:
            gapped = o <= level if leg.short else o >= level
            self.enter(leg, o if gapped else level, m, m + 1, f"re-entry {leg.reentries} at cost")

    # A leg waiting for its own range to break: enters on candle m at the range level.
    def check_leg_breakout(self, leg: _LegState, m: int):
        o, h, low = leg.prices[0][m], leg.prices[1][m], leg.prices[2][m]
        side = self.s.breakout_side
        up = side in ("high", "either") and h > leg.range_hi
        down = side in ("low", "either") and low < leg.range_lo
        if up:
            note = f"broke its range high {leg.range_hi:g}" + (" (low also broken in this minute)" if down else "")
            self.enter(leg, max(o, leg.range_hi), m, m + 1, note)
        elif down:
            self.enter(leg, min(o, leg.range_lo), m, m + 1, f"broke its range low {leg.range_lo:g}")

    # Whole-strategy M2M (before costs) when candle m closed: realised + open legs at the close.
    def m2m(self, m: int) -> float:
        open_pnl = sum((1 if not leg.short else -1) * leg.qty * (leg.prices[3][m] - leg.entry)
                       for leg in self.legs if leg.status == "open")
        return self.realised + open_pnl

    # The M2M stop level right now (stop loss, lock and trailing together), or None.
    def m2m_floor(self):
        s, levels = self.s, []
        if s.m2m_sl is not None:
            levels.append(-s.m2m_sl)
        if s.m2m_lock_at is not None and self.m2m_best >= s.m2m_lock_at:
            levels.append(s.m2m_lock_to)
        if s.m2m_trail_x is not None:
            start = s.m2m_lock_at if s.m2m_lock_at is not None else 0.0
            base = s.m2m_lock_to if s.m2m_lock_at is not None else -s.m2m_sl
            if self.m2m_best >= start:
                levels.append(base + math.floor((self.m2m_best - start) / s.m2m_trail_x + 1e-9) * s.m2m_trail_y)
        return max(levels) if levels else None

    # Check the whole strategy at the close of candle m; True when everything was closed.
    def check_m2m(self, m: int) -> bool:
        value = self.m2m(m)
        self.curve.append((self.at(m + 1).time(), value))
        s, floor = self.s, self.m2m_floor()
        reason = None
        if floor is not None and value <= floor:
            raised = s.m2m_sl is None or floor > -s.m2m_sl
            reason = "M2M_TRAIL" if raised else "M2M_SL"
        elif s.m2m_target is not None and value >= s.m2m_target:
            reason = "M2M_TARGET"
        if reason:
            note = f"strategy M2M Rs {value:,.2f} at {self.at(m + 1):%H:%M}"
            for leg in self.legs:
                if leg.status == "open":
                    self.close(leg, leg.prices[3][m], self.at(m + 1), reason, note)
                leg.status = "done"
            return True
        self.m2m_best = max(self.m2m_best, value)     # used from the next minute on
        return False

    # When the strategy enters: the entry minute, or the first minute after the range breaks.
    def plan_entry(self) -> int:
        s = self.s
        if s.entry_mode == "time":
            return minute_index(s.entry_time)
        r0, r1 = minute_index(s.range_start), minute_index(s.range_end)
        if s.range_source == "leg":
            k = r1 - 1
            for leg in self.legs:
                self.use_strike(leg, self.choose(leg, k))
                highs, lows = leg.prices[1][r0:r1], leg.prices[2][r0:r1]
                leg.range_hi, leg.range_lo = max(highs), min(lows)
                leg.status, leg.wake = "breakout", r1
            return r1
        closes = self.d.spot_series()
        hi, lo = np.nanmax(closes[r0:r1]), np.nanmin(closes[r0:r1])
        for m in range(r1, self.last):
            up = s.breakout_side in ("high", "either") and closes[m] > hi
            down = s.breakout_side in ("low", "either") and closes[m] < lo
            if up or down:
                note = (f"{s.underlying} closed {closes[m]:,.2f} at {self.at(m + 1):%H:%M}, "
                        f"{'above the range high' if up else 'below the range low'} "
                        f"({hi:,.2f} / {lo:,.2f})")
                for leg in self.legs:
                    leg.entry_note = note
                return m + 1
        raise ValueError(f"No breakout of the {s.underlying} range {hi:,.2f}-{lo:,.2f} before the exit time")

    # Run the day; returns (trades, MTM summary, spot at exit).
    def run(self):
        start = self.plan_entry()
        for leg in self.legs:
            if leg.status == "scheduled":
                leg.wake = start
        for m in range(start, self.last + 1):
            for leg in self.legs:
                if leg.status == "scheduled" and m >= leg.wake:
                    note = leg.entry_note if leg.reentries == 0 else f"re-execute {leg.reentries} with a new strike"
                    self.use_strike(leg, self.choose(leg, m - 1))
                    self.enter(leg, leg.prices[3][m - 1], m, m, note)
                    if len(self.curve) == 0 and all(x.status != "scheduled" for x in self.legs):
                        self.curve.append((self.at(m).time(), self.m2m(m - 1)))
            for leg in self.legs:
                if leg.status == "open" and m >= leg.monitor_from:
                    self.check_leg(leg, m)
            for leg in self.legs:
                if leg.status == "cost" and m >= leg.wake:
                    self.check_cost(leg, m)
                elif leg.status == "breakout" and m >= leg.wake:
                    self.check_leg_breakout(leg, m)
            self.apply_cost_moves()
            if self.first_entry is not None and self.check_m2m(m):
                break
        else:
            for leg in self.legs:
                if leg.status == "open":
                    self.close(leg, leg.prices[3][self.last], self.exit_at(), "TIME")
        if self.first_entry is None:
            raise ValueError("No leg broke its range before the exit time")
        return self.trades, self.summary(), self.d.spot_at_bar(self.last)

    # The day's mark-to-market summary: best and worst M2M and when they happened.
    def summary(self) -> dict:
        times, values = zip(*self.curve) if self.curve else ((self.first_entry.time(),), (0.0,))
        best, worst = int(np.argmax(values)), int(np.argmin(values))
        return {"date": self.d.day, "expiry": self.d.expiry, "entered_at": self.first_entry.time(),
                "spot_at_entry": round(self.spot_entry, 2) if self.spot_entry is not None else None,
                "max_profit": round(float(values[best]), 2), "max_profit_at": times[best],
                "max_loss": round(float(values[worst]), 2), "max_loss_at": times[worst],
                "pnl_at_exit_before_costs": round(sum(t.gross_pnl for t in self.trades), 2),
                "trades": len(self.trades)}


# Run the strategy on one day: returns (trades, MTM summary, spot at exit).
def run_day(settings: OptionsBacktestSettings, day: DayArrays) -> tuple[list[Trade], dict, float]:
    return _DayRun(settings, day).run()


# ---------------------------------------------------------------- the whole period

# The expiry traded on `day` (weekly/monthly, current/next), with the holiday shift.
def choose_expiry(settings: OptionsBacktestSettings, day: dt.date, trading_days: set) -> dt.date:
    flag = expiries.WEEK if settings.expiry_type == "weekly" else expiries.MONTH
    expiry = expiries.rule_expiry(settings.underlying, day, flag)
    if expiries.adjust_for_holidays(expiry, trading_days) < day:     # this week's expiry was moved earlier
        expiry = expiries.rule_expiry(settings.underlying, expiry + dt.timedelta(days=1), flag)
    if settings.expiry_which == "next":
        expiry = expiries.rule_expiry(settings.underlying, expiry + dt.timedelta(days=1), flag)
    return expiries.adjust_for_holidays(expiry, trading_days)


# The days to trade, each with its expiry, or the reason it is skipped.
def plan_days(settings: OptionsBacktestSettings, data) -> tuple[list[tuple], list[dict]]:
    stored = data.trading_days(settings.start, settings.end + dt.timedelta(days=45))
    known = set(stored)
    plan, skipped = [], []
    for day in stored:
        if day > settings.end or day < settings.start or day.weekday() not in settings.weekdays:
            continue
        try:
            expiry = choose_expiry(settings, day, known)
        except ValueError as err:
            skipped.append({"date": day, "reason": str(err)})
            continue
        if settings.skip_expiry_day and expiry == day:
            skipped.append({"date": day, "reason": "expiry day (skipped by setting)"})
            continue
        plan.append((day, expiry))
    return plan, skipped


# Load and convert one day (runs in a background thread).
def _load(data, day: dt.date, expiry: dt.date):
    df = data.load_day(day, expiry)
    return None if df is None or df.empty else DayArrays.from_frame(df, day, expiry)


# Run the backtest over the whole period; returns a BacktestResult (saved with save_run() when save=True).
def run_options_backtest(settings: OptionsBacktestSettings, data=None, save: bool = True,
                         strategy_id: int | None = None, url: str | None = None, progress=None) -> BacktestResult:
    """data: where the minutes come from (default: PostgreSQL). It needs trading_days(start, end)
    and load_day(day, expiry). progress(done, total) is called after each day."""
    data = data or StoredOptionData(settings.underlying, url)
    plan, skipped = plan_days(settings, data)
    trades, mtm, closes, demo = [], [], {}, False
    with ThreadPoolExecutor(max_workers=LOAD_THREADS) as pool:
        pending = [pool.submit(_load, data, d, e) for d, e in plan[:PREFETCH_DAYS]]
        for i, (day, expiry) in enumerate(plan):
            if i + PREFETCH_DAYS < len(plan):          # keep a few days loading ahead
                pending.append(pool.submit(_load, data, *plan[i + PREFETCH_DAYS]))
            arrays = pending[i].result()
            pending[i] = None
            if arrays is None:
                skipped.append({"date": day, "reason": f"no stored data for the {expiry:%d-%b-%Y} expiry"})
            else:
                demo = demo or arrays.is_demo
                try:
                    day_trades, day_mtm, close = run_day(settings, arrays)
                    trades += day_trades
                    mtm.append(day_mtm)
                    closes[day] = close
                except ValueError as err:
                    skipped.append({"date": day, "reason": str(err)})
            if progress:
                progress(i + 1, len(plan))
    result = BacktestResult.build(
        trades, settings.capital, settings.start, settings.end, name=settings.name,
        benchmark=pd.Series(closes, dtype=float) if closes else None,
        benchmark_name=f"{settings.underlying} (spot at exit time)", params=settings.to_params(), is_demo=demo)
    result.extras = {"skipped": pd.DataFrame(skipped, columns=["date", "reason"]).sort_values("date"),
                     "mtm": pd.DataFrame(mtm), "days_traded": len(mtm)}
    log.info("Options backtest %s: %d days traded, %d skipped, P&L %.2f", settings.name, len(mtm),
             len(skipped), result.metrics["total_pnl"])
    if save:
        from marketlens.backtest.store import save_run
        result.extras["run_id"] = save_run(strategy_id, {}, result, url)
    return result


# ---------------------------------------------------------------- page helpers

STRIKE_STEPS = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "SENSEX": 100}

# Ready-made strategies for the page: settings fields plus legs (dates and underlying are added later).
TEMPLATES = {
    "920_straddle": {
        "label": "9:20 short straddle (SL 25% per leg)",
        "entry_time": dt.time(9, 20), "exit_time": dt.time(15, 15),
        "legs": [LegSpec("SELL", "CE", sl=25), LegSpec("SELL", "PE", sl=25)]},
    "delta_strangle": {
        "label": "Short strangle at 0.20 delta (SL 30%)",
        "entry_time": dt.time(9, 20), "exit_time": dt.time(15, 15),
        "legs": [LegSpec("SELL", "CE", strike_rule="DELTA", value=0.20, sl=30),
                 LegSpec("SELL", "PE", strike_rule="DELTA", value=0.20, sl=30)]},
    "premium_strangle": {
        "label": "Rs 50 premium strangle (SL 50%, SL to cost)",
        "entry_time": dt.time(9, 20), "exit_time": dt.time(15, 15), "move_sl_to_cost": True,
        "legs": [LegSpec("SELL", "CE", strike_rule="PREMIUM", value=50, sl=50),
                 LegSpec("SELL", "PE", strike_rule="PREMIUM", value=50, sl=50)]},
    "iron_condor": {
        "label": "Iron condor: sell ATM ± 4, buy ATM ± 8 (M2M SL Rs 5,000)",
        "entry_time": dt.time(9, 30), "exit_time": dt.time(15, 15), "m2m_sl": 5000.0,
        "legs": [LegSpec("SELL", "CE", value=4), LegSpec("BUY", "CE", value=8),
                 LegSpec("SELL", "PE", value=-4), LegSpec("BUY", "PE", value=-8)]},
    "breakout_buying": {
        "label": "Breakout option buying: buy the ATM CE / PE when its 09:15-09:30 high breaks",
        "entry_time": dt.time(9, 30), "exit_time": dt.time(15, 15), "entry_mode": "breakout",
        "range_start": dt.time(9, 15), "range_end": dt.time(9, 30), "range_source": "leg", "breakout_side": "high",
        "legs": [LegSpec("BUY", "CE", sl=30, target=60, trail_x=10, trail_y=10),
                 LegSpec("BUY", "PE", sl=30, target=60, trail_x=10, trail_y=10)]},
}


# A template as settings (copies, so changing them never changes the template).
def template_settings(name: str, underlying: str, start: dt.date, end: dt.date, **extra) -> OptionsBacktestSettings:
    if name not in TEMPLATES:
        raise ValueError(f"Unknown template {name!r}")
    fields = {k: v for k, v in TEMPLATES[name].items() if k not in ("label", "legs")}
    legs = [LegSpec(**asdict(leg)) for leg in TEMPLATES[name]["legs"]]
    return OptionsBacktestSettings(underlying, start, end, legs, **{**fields, **extra})


# A leg from the page's input boxes: 0 means "off" for SL, target, trailing and lock.
def leg_from_values(values: dict) -> LegSpec:
    v = dict(values)
    for key in ("sl", "target", "trail_x", "trail_y", "lock_at", "lock_to"):
        if not v.get(key):
            v[key] = None
    if v.get("reentry", "none") == "none":
        v["max_reentries"] = 0
    known = LegSpec.__dataclass_fields__
    return LegSpec(**{k: val for k, val in v.items() if k in known})


# Legs from the Strategy builder (exact strikes) as ATM +/- N rules, using the spot when it was built.
def legs_from_builder(handoff: dict) -> list[LegSpec]:
    step = STRIKE_STEPS.get(handoff["underlying"].upper(), 50)
    atm = round(float(handoff["spot"]) / step) * step
    legs = []
    for leg in handoff["legs"]:
        if leg["option_type"] not in ("CE", "PE"):
            continue                       # futures legs are not backtested here
        legs.append(LegSpec(leg["side"], leg["option_type"], int(leg.get("lots", 1)),
                            value=round((float(leg["strike"]) - atm) / step)))
    if not legs:
        raise ValueError("The strategy has no option legs to backtest")
    return legs


# The settings again from the JSON saved with a run or a Strategy Library entry.
def settings_from_params(params: dict) -> OptionsBacktestSettings:
    p = dict(params)
    for key in ("entry_time", "exit_time", "range_start", "range_end"):
        if key in p:
            p[key] = dt.time.fromisoformat(p[key])
    for key in ("start", "end"):
        p[key] = dt.date.fromisoformat(p[key])
    p["weekdays"] = tuple(p.get("weekdays", (0, 1, 2, 3, 4)))
    p["costs"] = CostSettings(**p["costs"]) if "costs" in p else CostSettings()
    known = OptionsBacktestSettings.__dataclass_fields__
    return OptionsBacktestSettings(**{k: v for k, v in p.items() if k in known})


# The strike rule as one short word for the strategy_legs table: ATM+2, PREMIUM:50, DELTA:0.2.
def _rule_text(leg: LegSpec) -> str:
    if leg.strike_rule == "ATM":
        return f"ATM{int(leg.value):+d}" if leg.value else "ATM"
    return f"{leg.strike_rule}:{leg.value:g}"


# Save the backtest settings in the Strategy Library (module 10); the same name is replaced.
def save_backtest_strategy(name: str, settings: OptionsBacktestSettings, notes: str = "",
                           url: str | None = None) -> int:
    from sqlalchemy import delete, select

    from marketlens.db import get_session
    from marketlens.models import Strategy, StrategyLeg
    if not name.strip():
        raise ValueError("Give the strategy a name")
    expiry_rule = {("weekly", "current"): "WEEKLY", ("weekly", "next"): "NEXT_WEEKLY",
                   ("monthly", "current"): "MONTHLY", ("monthly", "next"): "NEXT_MONTHLY"}[
        (settings.expiry_type, settings.expiry_which)]
    params = {**settings.to_params(), "source": "options_backtester"}
    with get_session(url) as session:
        strategy = session.scalar(select(Strategy).where(Strategy.name == name.strip()))
        if strategy is None:
            strategy = Strategy(name=name.strip(), kind="options", params=params, description=notes or None)
            session.add(strategy)
            session.flush()
        else:
            strategy.params, strategy.description = params, notes or strategy.description
            session.execute(delete(StrategyLeg).where(StrategyLeg.strategy_id == strategy.id))
        for no, leg in enumerate(settings.legs, start=1):
            pct = leg.unit == "pct"
            session.add(StrategyLeg(
                strategy_id=strategy.id, leg_no=no, action=leg.side, instrument_kind=leg.option_type,
                strike_rule=_rule_text(leg), expiry_rule=expiry_rule, lots=leg.lots,
                stop_loss_pct=leg.sl if pct else None, target_pct=leg.target if pct else None,
                params=asdict(leg)))
        session.commit()
        return strategy.id


# Read a strategy saved by save_backtest_strategy back as settings.
def load_backtest_strategy(name: str, url: str | None = None) -> OptionsBacktestSettings:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Strategy
    with get_session(url) as session:
        strategy = session.scalar(select(Strategy).where(Strategy.name == name))
        if strategy is None or strategy.params.get("source") != "options_backtester":
            raise ValueError(f"No backtester strategy called {name!r}")
        return settings_from_params(strategy.params)


# How many days between start and end have stored option minutes (0 = download first).
def stored_day_count(underlying: str, start: dt.date, end: dt.date, url: str | None = None) -> int:
    return len(StoredOptionData(underlying, url).trading_days(start, end))
