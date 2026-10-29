"""Option Simulator (module 5): replay a past trading day and trade it with virtual money.

    day = load_day("NIFTY", date(2026, 10, 15))           # one day of stored option minutes
    sim = SimSession(day, "NIFTY", lot_size=65)
    sim.place("SELL", 25000, "CE", lots=1, sl_points=30)   # at the current replay minute's price
    sim.advance(5)                                          # 5 minutes later: SL / target checked
    sim.exit_all()
    save_session(sim)                                       # simulator_sessions + simulator_trades

THE CLOCK: minute M means the replay time is 09:15 + M minutes, so the candles 0 .. M-1 have
finished and are all you can see. Nothing from candle M or later is ever shown or used:
  - an order fills at the close of candle M-1 (plus slippage);
  - stop losses and targets are orders waiting in the market, so while the clock moves from M to
    M + 5 they are checked on candles M .. M + 4 (high and low; if both are touched in one minute
    the SL is assumed, the worse case).
"""
import datetime as dt
import math
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from marketlens.backtest.engine import order_costs
from marketlens.backtest.options_bt import BARS, MARKET_CLOSE, MARKET_OPEN, DayArrays, fill_price, minute_index
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings, to_paise
from marketlens.options.chain import DEFAULT_LOT_SIZES
from marketlens.options.pricing import RISK_FREE_RATE, greeks

SPEEDS = [1, 2, 5, 10, 30]          # replay minutes per real second
FIRST_MINUTE = 1                    # 09:16: the first moment a candle (09:15) has finished
LAST_MINUTE = BARS                  # 15:30: the day is over
BOTH_HIT = "SL and target both touched in this minute: assumed SL (worse case)"


# ---------------------------------------------------------------- the clock

@dataclass
class ReplayClock:
    """The replay time, kept in st.session_state between page refreshes.

    minute: 09:15 + minute is "now" (1 = 09:16 ... 375 = 15:30).
    While playing, advance(wall_time) moves `speed` replay minutes per real second.
    """
    minute: int = FIRST_MINUTE
    playing: bool = False
    speed: int = 1
    last_tick: float | None = None       # real time (time.monotonic()) the clock last moved

    # The replay time as a clock time.
    def time(self) -> dt.time:
        return (dt.datetime.combine(dt.date.today(), MARKET_OPEN) + dt.timedelta(minutes=self.minute)).time()

    # True once the replay has reached 15:30.
    @property
    def finished(self) -> bool:
        return self.minute >= LAST_MINUTE

    # Move n minutes (never past 15:30, never before 09:16); returns how far it really moved.
    def step(self, n: int = 1) -> int:
        target = min(max(self.minute + n, FIRST_MINUTE), LAST_MINUTE)
        moved, self.minute = target - self.minute, target
        if self.finished:
            self.playing = False
        return moved

    # Jump straight to a clock time (09:16 .. 15:30); returns how far it moved (can be negative).
    def jump(self, when: dt.time) -> int:
        return self.step(minute_index(when) - self.minute)

    # Start playing from now.
    def play(self, wall: float | None = None):
        if not self.finished:
            self.playing, self.last_tick = True, time.monotonic() if wall is None else wall

    # Stop playing.
    def pause(self):
        self.playing, self.last_tick = False, None

    # Change the speed (1, 2, 5, 10 or 30 replay minutes per second).
    def set_speed(self, speed: int, wall: float | None = None):
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        self.speed = speed
        if self.playing:
            self.last_tick = time.monotonic() if wall is None else wall

    # While playing: how many minutes are due since the last tick (part-minutes are kept for later).
    def due(self, wall: float | None = None) -> int:
        if not self.playing or self.last_tick is None:
            return 0
        wall = time.monotonic() if wall is None else wall
        minutes = math.floor((wall - self.last_tick) * self.speed + 1e-9)
        if minutes > 0:
            self.last_tick += minutes / self.speed
        return minutes


# ---------------------------------------------------------------- virtual positions

@dataclass
class SimPosition:
    """One virtual position: opened at one minute, closed at a later one (or still open)."""
    id: int
    symbol: str
    strike: float
    option_type: str
    side: str
    lots: int
    qty: int
    entry_price: float
    entry_minute: int
    sl: float | None = None              # price level
    target: float | None = None          # price level
    exit_price: float | None = None
    exit_minute: int | None = None
    reason: str | None = None            # MANUAL, SL, TARGET, EXIT_ALL, DAY_END
    note: str = ""
    entry_costs: float = 0.0
    exit_costs: float = 0.0

    # +1 for bought, -1 for sold.
    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1

    # True until it is closed.
    @property
    def is_open(self) -> bool:
        return self.exit_minute is None

    # P&L before costs at a price (the exit price once closed).
    def gross(self, price: float | None = None) -> float:
        price = self.exit_price if price is None else price
        return to_paise(self.sign * (price - self.entry_price) * self.qty)


class SimSession:
    """A replay of one day with virtual trading. Costs come from costs.py (CostSettings);
    slippage is in the fill prices, so the brokerage and taxes are worked out without it."""

    def __init__(self, day: DayArrays, underlying: str, lot_size: int | None = None,
                 costs: CostSettings | None = None, capital: float = 500_000, clock: ReplayClock | None = None):
        self.day, self.underlying = day, underlying.upper()
        self.lot_size = int(lot_size or DEFAULT_LOT_SIZES.get(self.underlying, 1))
        self.costs = costs or CostSettings()
        self.no_slip = CostSettings(**{**self.costs.__dict__, "slippage_value": 0.0})
        self.capital = capital
        self.clock = clock or ReplayClock()
        self.positions: list[SimPosition] = []
        self.pnl_curve: list[tuple[int, float]] = [(self.clock.minute, 0.0)]
        self.saved_id: int | None = None

    # ---- what is known now ----

    # Index of the last finished candle (the replay time minus one minute).
    @property
    def known(self) -> int:
        return self.clock.minute - 1

    # The replay time as a date-time.
    def now(self, minute: int | None = None) -> dt.datetime:
        minute = self.clock.minute if minute is None else minute
        return dt.datetime.combine(self.day.day, MARKET_OPEN, IST) + dt.timedelta(minutes=minute)

    # The underlying's price now.
    def spot(self) -> float:
        return self.day.spot_at_bar(self.known)

    # The last traded price of one option now (close of the last finished candle).
    def quote(self, strike: float, option_type: str) -> float:
        t, s = (0 if option_type.upper() == "CE" else 1), self.day.strike_row(float(strike))
        price = self.day.price[t, s, self.known]
        if np.isnan(price):
            raise ValueError(f"{option_type} {strike:g} has not traded yet at {self.clock.time():%H:%M}")
        return float(price)

    # The option chain as it looked now: LTP, IV, delta and OI for calls and puts, one row per strike.
    def chain(self) -> pd.DataFrame:
        k, d = self.known, self.day
        spot = self.spot()
        years = max((dt.datetime.combine(d.expiry, MARKET_CLOSE, IST) - self.now()).total_seconds(), 60) / (365 * 86400)
        rows = []
        for s, strike in enumerate(d.strikes):
            row = {"strike": float(strike)}
            for t, kind in enumerate(("ce", "pe")):
                price, iv = d.price[t, s, k], d.iv[t, s, k]
                fresh = d.age[t, s, k] <= 30
                row[f"{kind}_ltp"] = float(price) if fresh and not np.isnan(price) else None
                row[f"{kind}_iv"] = float(iv) if row[f"{kind}_ltp"] and not np.isnan(iv) else None
                row[f"{kind}_delta"] = (greeks(spot, strike, years, RISK_FREE_RATE, iv / 100, kind.upper())["delta"]
                                        if row[f"{kind}_iv"] else None)
                oi = d.oi[t, s, k] if d.oi is not None else np.nan
                row[f"{kind}_oi"] = float(oi) if row[f"{kind}_ltp"] and not np.isnan(oi) else None
            if row["ce_ltp"] or row["pe_ltp"]:
                rows.append(row)
        return pd.DataFrame(rows)

    # Index candles so far, grouped into `every`-minute candles (from the spot closes; no future minutes).
    def spot_candles(self, every: int = 5) -> pd.DataFrame:
        closes = self.day.spot_series()[: self.clock.minute]
        times = [self.now(m) for m in range(len(closes))]
        frame = pd.DataFrame({"ts": times, "close": closes}).dropna()
        if frame.empty:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close"])
        grouped = frame.groupby(frame["ts"].dt.floor(f"{every}min"))["close"]
        return pd.DataFrame({"open": grouped.first(), "high": grouped.max(), "low": grouped.min(),
                             "close": grouped.last()}).reset_index()

    # ---- orders ----

    # Buy or sell at the current minute's price; sl_points / target_points are distances from the fill.
    def place(self, side: str, strike: float, option_type: str, lots: int = 1,
              sl_points: float | None = None, target_points: float | None = None) -> SimPosition:
        side, option_type = side.upper(), option_type.upper()
        if side not in ("BUY", "SELL") or option_type not in ("CE", "PE"):
            raise ValueError("Choose BUY/SELL and CE/PE")
        if lots < 1:
            raise ValueError("lots must be 1 or more")
        if self.clock.finished:
            raise ValueError("The day is over: no more orders")
        if any(v is not None and v <= 0 for v in (sl_points, target_points)):
            raise ValueError("SL and target are distances in points above 0 (leave empty for none)")
        price = fill_price(self.quote(strike, option_type), side, self.costs)
        qty = lots * self.lot_size
        away = 1 if side == "SELL" else -1           # the SL is above a sold option, below a bought one
        pos = SimPosition(len(self.positions) + 1, f"{self.underlying} {strike:g} {option_type} {self.day.expiry:%d-%b-%Y}",
                          float(strike), option_type, side, lots, qty, price, self.clock.minute,
                          sl=price + away * sl_points if sl_points else None,
                          target=price - away * target_points if target_points else None)
        self.positions.append(pos)
        return pos

    # Close one position at the current minute's price.
    def exit(self, pos_id: int, reason: str = "MANUAL") -> SimPosition:
        pos = next((p for p in self.positions if p.id == pos_id and p.is_open), None)
        if pos is None:
            raise ValueError(f"No open position {pos_id}")
        self._close(pos, self.quote(pos.strike, pos.option_type), self.clock.minute, reason)
        return pos

    # Close every open position now.
    def exit_all(self, reason: str = "EXIT_ALL") -> int:
        open_ = [p for p in self.positions if p.is_open]
        for pos in open_:
            self.exit(pos.id, reason)
        self._record()
        return len(open_)

    # Fill the closing order (mark = market price before slippage) and work out both orders' costs.
    def _close(self, pos: SimPosition, mark: float, minute: int, reason: str, note: str = ""):
        closing = "BUY" if pos.side == "SELL" else "SELL"
        pos.exit_price, pos.exit_minute, pos.reason, pos.note = fill_price(mark, closing, self.costs), minute, reason, note
        both = order_costs("options", pos.side, pos.entry_price, pos.exit_price, pos.qty, self.no_slip)
        pos.entry_costs = order_costs("options", pos.side, pos.entry_price, 0, pos.qty, self.no_slip)
        pos.exit_costs = to_paise(both - pos.entry_costs)

    # ---- moving the clock ----

    # Move the clock n minutes; stop losses and targets are checked on every candle that finishes.
    def advance(self, n: int = 1) -> int:
        moved = 0
        for _ in range(max(n, 0)):
            if self.clock.finished:
                break
            m = self.clock.minute                 # candle m finishes as the clock moves to m + 1
            for pos in self.positions:
                if pos.is_open and m >= pos.entry_minute:
                    self._check(pos, m)
            self.clock.step(1)
            moved += 1
            self._record()
        if self.clock.finished:
            for pos in self.positions:
                if pos.is_open:
                    self._close(pos, self.quote(pos.strike, pos.option_type), LAST_MINUTE, "DAY_END")
            self._record()
        return moved

    # Jump to a time. Forward: every minute in between is replayed (SL / target still work).
    # Backward: only allowed with no positions, because the past cannot be undone.
    def jump(self, when: dt.time) -> int:
        target = min(max(minute_index(when), FIRST_MINUTE), LAST_MINUTE)
        if target < self.clock.minute:
            if self.positions:
                raise ValueError("You have trades in this session: start a new session to go back in time")
            moved = self.clock.step(target - self.clock.minute)
            self.pnl_curve = [(self.clock.minute, 0.0)]
            return moved
        return self.advance(target - self.clock.minute)

    # Stop loss and target on candle m (high and low), like the backtester.
    def _check(self, pos: SimPosition, m: int):
        t, s = (0 if pos.option_type == "CE" else 1), self.day.strike_row(pos.strike)
        o, h, low = (float(arr[t, s, m]) for arr in (self.day.open, self.day.high, self.day.low))
        if np.isnan(h):
            return
        short = pos.side == "SELL"
        sl_hit = pos.sl is not None and (h >= pos.sl if short else low <= pos.sl)
        tg_hit = pos.target is not None and (low <= pos.target if short else h >= pos.target)
        if sl_hit:
            gapped = o >= pos.sl if short else o <= pos.sl
            note = "; ".join(x for x in (BOTH_HIT if tg_hit else "", "opened past the SL: filled at the open" if gapped else "") if x)
            self._close(pos, o if gapped else pos.sl, m, "SL", note)
        elif tg_hit:
            gapped = o <= pos.target if short else o >= pos.target
            self._close(pos, o if gapped else pos.target, m, "TARGET", "opened past the target: filled at the open" if gapped else "")

    # Add the current P&L to the P&L curve.
    def _record(self):
        point = (self.clock.minute, self.pnl())
        if self.pnl_curve and self.pnl_curve[-1][0] == point[0]:
            self.pnl_curve[-1] = point
        else:
            self.pnl_curve.append(point)

    # ---- P&L ----

    # Total P&L now: closed positions after costs + open positions at the current price (after entry costs).
    def pnl(self) -> float:
        total = 0.0
        for pos in self.positions:
            if pos.is_open:
                entry_costs = order_costs("options", pos.side, pos.entry_price, 0, pos.qty, self.no_slip)
                total += pos.gross(self.quote(pos.strike, pos.option_type)) - entry_costs
            else:
                total += pos.gross() - pos.entry_costs - pos.exit_costs
        return to_paise(total)

    # The positions table: open ones at the current price, closed ones at their exit.
    def positions_frame(self) -> pd.DataFrame:
        rows = []
        for p in self.positions:
            ltp = self.quote(p.strike, p.option_type) if p.is_open else p.exit_price
            costs = p.entry_costs + p.exit_costs if not p.is_open else order_costs("options", p.side, p.entry_price, 0, p.qty, self.no_slip)
            rows.append({"id": p.id, "symbol": p.symbol, "side": p.side, "lots": p.lots, "qty": p.qty,
                         "entry_time": self.now(p.entry_minute).time(), "entry_price": p.entry_price,
                         "ltp_or_exit": ltp, "sl": p.sl, "target": p.target, "status": "OPEN" if p.is_open else p.reason,
                         "exit_time": self.now(p.exit_minute).time() if not p.is_open else None,
                         "gross_pnl": p.gross(ltp), "costs": to_paise(costs), "net_pnl": to_paise(p.gross(ltp) - costs),
                         "note": p.note or None})
        return pd.DataFrame(rows)

    # The P&L curve as a table (time, pnl).
    def curve_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"time": [self.now(m) for m, _ in self.pnl_curve], "pnl": [v for _, v in self.pnl_curve]})

    # Strategy-builder legs for the open positions (for the mini payoff chart).
    def open_legs(self) -> list:
        from marketlens.options.strategies import Leg
        legs = []
        for p in self.positions:
            if p.is_open:
                t, s = (0 if p.option_type == "CE" else 1), self.day.strike_row(p.strike)
                iv = self.day.iv[t, s, self.known]
                legs.append(Leg(p.side, p.option_type, p.strike, self.day.expiry, p.lots, p.entry_price,
                                self.lot_size, None if np.isnan(iv) else float(iv)))
        return legs

    # The end-of-session summary.
    def summary(self) -> dict:
        closed = [p for p in self.positions if not p.is_open]
        nets = [p.gross() - p.entry_costs - p.exit_costs for p in closed]
        curve = [v for _, v in self.pnl_curve]
        return {"pnl": self.pnl(), "trades": len(self.positions), "closed": len(closed),
                "winners": sum(n > 0 for n in nets), "win_rate_pct": (sum(n > 0 for n in nets) / len(nets) * 100) if nets else None,
                "costs": to_paise(sum(p.entry_costs + p.exit_costs for p in closed)),
                "best_pnl": max(curve), "worst_pnl": min(curve)}


# ---------------------------------------------------------------- data

# Days with stored option minutes for an underlying (newest last).
def available_days(underlying: str, url: str | None = None) -> list[dt.date]:
    from marketlens.ingest.options import stored_days
    return stored_days(underlying, dt.date(2015, 1, 1), dt.date.today() + dt.timedelta(days=1), url)


# Expiries stored for a day (nearest first).
def available_expiries(underlying: str, day: dt.date, url: str | None = None) -> list[dt.date]:
    from marketlens.ingest.options import stored_expiries
    return stored_expiries(underlying, day, url)


# One day of one expiry as DayArrays (raises ValueError when nothing is stored).
def load_day(underlying: str, day: dt.date, expiry: dt.date | None = None, url: str | None = None) -> DayArrays:
    from marketlens.ingest.options import load_option_day
    frame = load_option_day(underlying, day, expiry, url)
    if frame.empty:
        raise ValueError(f"No {underlying} option minutes stored for {day:%d-%b-%Y}")
    return DayArrays.from_frame(frame, day, frame.attrs.get("expiry", expiry))


# ---------------------------------------------------------------- saving

# Save the session and every order (opening and closing) into simulator_sessions / simulator_trades.
def save_session(sim: SimSession, notes: str = "", url: str | None = None) -> int:
    from sqlalchemy import delete, select

    from marketlens.db import get_session
    from marketlens.ingest.options import _underlying_id
    from marketlens.models import OptionContract, SimulatorSession, SimulatorTrade
    with get_session(url) as session:
        underlying_id = _underlying_id(session, sim.underlying)
        contracts = {(float(k), t): cid for cid, k, t in session.execute(
            select(OptionContract.id, OptionContract.strike, OptionContract.option_type).where(
                OptionContract.underlying_id == underlying_id, OptionContract.expiry == sim.day.expiry))}
        settings = {"expiry": sim.day.expiry.isoformat(), "lot_size": sim.lot_size, "costs": sim.costs.__dict__,
                    "stopped_at": f"{sim.clock.time():%H:%M}", "is_demo": sim.day.is_demo}
        row = session.get(SimulatorSession, sim.saved_id) if sim.saved_id else None
        if row is None:
            row = SimulatorSession(underlying_id=underlying_id, trade_date=sim.day.day, capital=sim.capital)
            session.add(row)
        else:
            session.execute(delete(SimulatorTrade).where(SimulatorTrade.session_id == row.id))
        row.pnl, row.settings, row.notes = sim.pnl(), settings, notes or row.notes
        row.ended_at = dt.datetime.now(dt.timezone.utc)
        session.flush()
        orders = []
        for p in sim.positions:
            cid = contracts.get((p.strike, p.option_type))
            orders.append(SimulatorTrade(session_id=row.id, contract_id=cid, symbol=p.symbol, side=p.side, qty=p.qty,
                                         price=p.entry_price, market_time=sim.now(p.entry_minute),
                                         costs=p.entry_costs or None, reason="OPEN"))
            if not p.is_open:
                orders.append(SimulatorTrade(session_id=row.id, contract_id=cid, symbol=p.symbol,
                                             side="BUY" if p.side == "SELL" else "SELL", qty=p.qty, price=p.exit_price,
                                             market_time=sim.now(p.exit_minute), costs=p.exit_costs, reason=p.reason,
                                             pnl=to_paise(p.gross() - p.entry_costs - p.exit_costs)))
        session.add_all(orders)
        session.commit()
        sim.saved_id = row.id
        return row.id


# Past sessions, newest first.
def list_sessions(limit: int = 50, url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import func, select

    from marketlens.db import get_session
    from marketlens.models import Instrument, SimulatorSession, SimulatorTrade
    with get_session(url) as session:
        rows = session.execute(
            select(SimulatorSession, Instrument.symbol, func.count(SimulatorTrade.id))
            .join(Instrument, Instrument.id == SimulatorSession.underlying_id)
            .outerjoin(SimulatorTrade, SimulatorTrade.session_id == SimulatorSession.id)
            .group_by(SimulatorSession.id, Instrument.symbol).order_by(SimulatorSession.id.desc()).limit(limit)).all()
        return pd.DataFrame([{"id": s.id, "underlying": sym, "day": s.trade_date, "pnl": float(s.pnl or 0),
                              "orders": n, "stopped_at": (s.settings or {}).get("stopped_at"),
                              "demo": (s.settings or {}).get("is_demo"), "saved": s.ended_at, "notes": s.notes}
                             for s, sym, n in rows],
                            columns=["id", "underlying", "day", "pnl", "orders", "stopped_at", "demo", "saved", "notes"])


# The orders of one saved session.
def session_orders(session_id: int, url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import SimulatorTrade
    with get_session(url) as session:
        rows = session.scalars(select(SimulatorTrade).where(SimulatorTrade.session_id == session_id)
                               .order_by(SimulatorTrade.market_time, SimulatorTrade.id)).all()
        return pd.DataFrame([{"time": r.market_time, "symbol": r.symbol, "side": r.side, "qty": r.qty,
                              "price": float(r.price), "reason": r.reason, "costs": float(r.costs or 0),
                              "pnl": float(r.pnl) if r.pnl is not None else None} for r in rows])
