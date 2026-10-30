"""Forward testing (module 6): run a saved strategy on today's live market with virtual money.

Run it in the background (it keeps going when the browser is closed):
    python -m marketlens.trading.forward --strategy demo --once --at "2026-10-28 09:18"   # practice now
    python -m marketlens.trading.forward --strategy 3                              # your saved strategy id 3
    python -m marketlens.trading.forward --strategy 3 --once                       # one cycle (testing)

`--strategy demo` uses a built-in practice strategy (the 9:20 NIFTY short straddle) on DEMO prices.
When your own strategies are saved, replace `demo` with their id from the Forward Test page.

Every minute in market hours:
  1. Options strategy (saved from the Options Backtester): take an option-chain snapshot (Dhan, or
     DEMO when the keys are not set) and add it to today's minute candles. Then run today, up to this
     minute, with the BACKTESTER'S OWN RULE CODE (options_bt.run_day_until): the same strike choice,
     SL, target, trailing, re-entry, breakout and M2M rules. Every entry or exit that has newly
     appeared becomes an order on the PaperBroker. So live and backtest behave the same; only the
     fill prices differ (the paper broker fills at the live price plus slippage).
  2. Indicator strategy: on each finished candle, EMA cross or RSI signals buy / sell; square off at
     the exit time.
  3. The day's forward_test_runs row gets the state the page shows (waiting for entry time, in trade,
     done for the day), today's P&L, open legs and today's trades with exit reasons.

The page asks the process to stop by setting status = "stopping"; the process then exits all
positions, marks the day "stopped" and ends.
"""
import argparse
import csv
import datetime as dt
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from marketlens import config
from marketlens.backtest import options_bt as ob
from marketlens.broker.candles import IST
from marketlens.broker.paper import PaperBroker
from marketlens.costs import CostSettings, to_paise
from marketlens.logger import get_logger
from marketlens.signals.indicators import ema, rsi

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "forward"          # today's recorded option minutes (CSV per strategy)
LOG_DIR = PROJECT_ROOT / "logs"
ALIVE_SECONDS = 150                                    # no heartbeat for this long = not running
STATES = {"waiting": "Waiting for the entry time", "watching": "Watching for the range breakout",
          "in_trade": "In trade", "done": "Done for the day", "closed": "Market closed",
          "stopped": "Stopped", "error": "Error (see the message)"}
INDICATOR_RULES = {"ema_cross": "EMA fast crosses EMA slow", "rsi": "RSI leaves oversold / overbought"}


# ---------------------------------------------------------------- indicator strategies

@dataclass
class IndicatorStrategy:
    """A simple indicator strategy on one stock or index (module 7 will build richer ones).

    ema_cross: BUY when the fast EMA crosses above the slow EMA, SELL when it crosses below.
    rsi:       BUY when RSI rises back above rsi_buy, SELL when it falls back below rsi_sell.
    Signals use finished candles only. Short selling only when allow_short is True.
    """
    symbol: str
    qty: int = 1
    rule: str = "ema_cross"
    fast: int = 9
    slow: int = 21
    rsi_length: int = 14
    rsi_buy: float = 30.0
    rsi_sell: float = 70.0
    timeframe: int = 5
    product: str = "INTRADAY"
    start_time: dt.time = dt.time(9, 20)
    exit_time: dt.time = dt.time(15, 15)
    allow_short: bool = False
    capital: float = 500_000
    costs: CostSettings = field(default_factory=CostSettings)

    # Check the settings.
    def __post_init__(self):
        self.symbol, self.rule = self.symbol.upper().strip(), self.rule.lower()
        if self.rule not in INDICATOR_RULES:
            raise ValueError(f"rule must be one of {list(INDICATOR_RULES)}")
        if self.qty < 1 or self.fast < 1 or self.slow <= self.fast:
            raise ValueError("qty must be 1 or more and the slow EMA longer than the fast one")
        if self.timeframe not in (1, 5, 15, 25, 60):
            raise ValueError("timeframe must be 1, 5, 15, 25 or 60 minutes")
        if not (ob.MARKET_OPEN < self.start_time < self.exit_time <= ob.MARKET_CLOSE):
            raise ValueError("start must be after 09:15 and before the exit time")

    # BUY, SELL or None from the closes of finished candles (looks at the last candle only).
    def signal(self, closes: pd.Series) -> str | None:
        closes = closes.dropna()
        if self.rule == "ema_cross":
            if len(closes) < self.slow + 1:
                return None
            diff = ema(closes, self.fast) - ema(closes, self.slow)
            before, now = diff.iloc[-2], diff.iloc[-1]
            if before <= 0 < now:
                return "BUY"
            if before >= 0 > now:
                return "SELL"
            return None
        if len(closes) < self.rsi_length + 2:
            return None
        r = rsi(closes, self.rsi_length)
        before, now = r.iloc[-2], r.iloc[-1]
        if before < self.rsi_buy <= now:
            return "BUY"
        if before > self.rsi_sell >= now:
            return "SELL"
        return None

    # The settings as JSON for the Strategy Library.
    def to_params(self) -> dict:
        p = asdict(self)
        p["start_time"], p["exit_time"] = f"{self.start_time:%H:%M}", f"{self.exit_time:%H:%M}"
        return {**p, "source": "forward_indicator"}

    # Back from the Strategy Library JSON.
    @classmethod
    def from_params(cls, params: dict) -> "IndicatorStrategy":
        p = {k: v for k, v in params.items() if k in cls.__dataclass_fields__}
        p["start_time"], p["exit_time"] = dt.time.fromisoformat(p["start_time"]), dt.time.fromisoformat(p["exit_time"])
        p["costs"] = CostSettings(**p["costs"]) if isinstance(p.get("costs"), dict) else CostSettings()
        return cls(**p)


# Save an indicator strategy in the Strategy Library (the same name is replaced); returns its id.
def save_indicator_strategy(name: str, strategy: IndicatorStrategy, url: str | None = None) -> int:
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
        row.kind, row.params = "indicator", strategy.to_params()
        session.commit()
        return row.id


# Strategies the forward tester can run: saved by the Options Backtester, or indicator strategies.
def list_strategies(url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Strategy
    with get_session(url) as session:
        rows = session.scalars(select(Strategy).order_by(Strategy.name)).all()
        out = [{"id": r.id, "name": r.name, "kind": r.kind,
                "underlying": (r.params or {}).get("underlying") or (r.params or {}).get("symbol")}
               for r in rows if (r.params or {}).get("source") in ("options_backtester", "forward_indicator")]
    return pd.DataFrame(out, columns=["id", "name", "kind", "underlying"])


# A saved strategy as ("options", OptionsBacktestSettings) or ("indicator", IndicatorStrategy).
def load_strategy(strategy_id: int, url: str | None = None) -> tuple[str, str, object]:
    from marketlens.db import get_session
    from marketlens.models import Strategy
    with get_session(url) as session:
        row = session.get(Strategy, strategy_id)
        if row is None:
            raise ValueError(f"No strategy with id {strategy_id}")
        name, params = row.name, dict(row.params or {})
    if params.get("source") == "options_backtester":
        return "options", name, ob.settings_from_params(params)
    if params.get("source") == "forward_indicator":
        return "indicator", name, IndicatorStrategy.from_params(params)
    raise ValueError(f"Strategy {name!r} was not saved by the Options Backtester or as an indicator strategy")


# ---------------------------------------------------------------- the built-in practice strategy

DEMO_STRATEGY_NAME = "DEMO 9:20 short straddle (practice)"


# Put the practice strategy in the Strategy Library (once) and return its id.
def ensure_demo_strategy(url: str | None = None) -> int:
    """The 9:20 NIFTY short straddle template (SL 25% per leg). It always runs on DEMO prices."""
    today = dt.date.today()
    settings = ob.template_settings("920_straddle", "NIFTY", today, today)
    return ob.save_backtest_strategy(DEMO_STRATEGY_NAME, settings, url=url,
                                     notes="Built-in practice strategy: always uses DEMO prices.")


# The id from --strategy: a number, or "demo" for the built-in practice strategy.
def resolve_strategy(value: str, url: str | None = None) -> int:
    if str(value).strip().lower() == "demo":
        return ensure_demo_strategy(url)
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"--strategy must be a number from the Forward Test page, or 'demo' (not {value!r})")


# ---------------------------------------------------------------- live minute candles

class LiveMinutes:
    """Today's option minute candles, built from option-chain snapshots.

    A snapshot taken at 09:20:05 is the price when the 09:19 candle closed, so it becomes candle 09:19
    (the backtester's rule: a decision at 09:20 uses the 09:19 close). Several snapshots in one minute
    make that candle's open / high / low / close. Every snapshot is also written to a CSV file, so a
    restarted process rebuilds the same candles and makes the same decisions.
    """
    FIELDS = ["ts", "strike", "option_type", "ltp", "iv", "oi", "spot"]

    def __init__(self, day: dt.date, expiry: dt.date, path: Path | None = None):
        self.day, self.expiry, self.path = day, expiry, path
        self.bars: dict[tuple, list] = {}          # (candle, strike, type) -> [open, high, low, close, iv, oi]
        self.spots: dict[int, float] = {}
        self.latest: dict[tuple, float] = {}       # (strike, type) -> last price
        if path is not None and path.exists():
            with path.open(newline="") as f:
                for row in csv.DictReader(f):
                    self._add(dt.datetime.fromisoformat(row["ts"]), float(row["strike"]), row["option_type"],
                              float(row["ltp"]), _num(row["iv"]), _num(row["oi"]), _num(row["spot"]))

    # The candle a snapshot at `when` closes: minute index of the nearest whole minute, minus one.
    @staticmethod
    def candle_of(when: dt.datetime) -> int:
        start = dt.datetime.combine(when.date(), ob.MARKET_OPEN, when.tzinfo)
        return round((when - start).total_seconds() / 60) - 1

    # Add one option's price at one moment.
    def _add(self, when, strike, kind, ltp, iv, oi, spot):
        m = self.candle_of(when)
        if not 0 <= m < ob.BARS or ltp is None or ltp <= 0:
            return
        bar = self.bars.get((m, strike, kind))
        if bar is None:
            self.bars[(m, strike, kind)] = [ltp, ltp, ltp, ltp, iv, oi]
        else:
            bar[1], bar[2], bar[3], bar[4], bar[5] = max(bar[1], ltp), min(bar[2], ltp), ltp, iv, oi
        if spot:
            self.spots[m] = spot
        self.latest[(strike, kind)] = ltp

    # Add a whole option chain snapshot (and save it to the CSV file).
    def add_chain(self, chain, when: dt.datetime):
        rows = []
        for r in chain.table.itertuples(index=False):
            for kind in ("ce", "pe"):
                ltp = getattr(r, f"{kind}_ltp")
                if ltp is None or pd.isna(ltp) or ltp <= 0:
                    continue
                iv, oi = getattr(r, f"{kind}_iv", None), getattr(r, f"{kind}_oi", None)
                values = (when, float(r.strike), kind.upper(), float(ltp), _num(iv), _num(oi), float(chain.spot))
                self._add(*values)
                rows.append(values)
        if self.path is not None and rows:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            new = not self.path.exists()
            with self.path.open("a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(self.FIELDS)
                w.writerows([[v[0].isoformat(), *v[1:]] for v in rows])

    # The candles as a load_option_day()-style table (for DayArrays.from_frame).
    def frame(self) -> pd.DataFrame:
        start = dt.datetime.combine(self.day, ob.MARKET_OPEN, IST)
        rows = [{"ts": start + dt.timedelta(minutes=m), "strike": k, "option_type": t, "open": b[0], "high": b[1],
                 "low": b[2], "close": b[3], "volume": 0, "oi": b[5], "iv": b[4], "spot": self.spots.get(m),
                 "is_demo": False} for (m, k, t), b in sorted(self.bars.items())]
        return pd.DataFrame(rows, columns=["ts", "strike", "option_type", "open", "high", "low", "close", "volume",
                                           "oi", "iv", "spot", "is_demo"])


# A number from a CSV cell or a table (None for empty / NaN).
def _num(value):
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    return float(value)


# ---------------------------------------------------------------- the runner

# Dhan when the keys are set, else DEMO prices that move every minute.
def live_client(now: dt.datetime | None = None):
    from marketlens.broker.dhan_data import DhanDataClient
    from marketlens.broker.demo_data import DemoDataClient
    dhan = DhanDataClient()
    if dhan.is_ready() and now is None:
        return dhan
    return DemoDataClient(now=now, moving=True)


class ForwardRunner:
    """Runs one saved strategy, one cycle per minute (cycle() is called by the loop in main())."""

    def __init__(self, strategy_id: int, capital: float | None = None, client=None, url: str | None = None,
                 clock=None, data_dir: Path | None = None):
        self.strategy_id, self.url = strategy_id, url
        self.kind, self.name, self.strategy = load_strategy(strategy_id, url)
        if self.name == DEMO_STRATEGY_NAME and client is None:     # the practice strategy: always DEMO prices
            from marketlens.broker.demo_data import DemoDataClient
            client = DemoDataClient(moving=True)
        self.client = client or live_client()
        self.clock = clock or (lambda: dt.datetime.now(IST))
        self.data_dir = data_dir or DATA_DIR
        self.capital = float(capital or getattr(self.strategy, "capital", None) or config.PAPER_CAPITAL)
        self.broker = PaperBroker(capital=self.capital, costs=self.strategy.costs, url=url, strategy_id=strategy_id)
        self.minutes: LiveMinutes | None = None

    # ---- the day's row ----

    # Today's forward_test_runs row (made on the first cycle of the day).
    def _day_row(self, day: dt.date):
        from sqlalchemy import select, update

        from marketlens.db import get_session
        from marketlens.models import ForwardTestRun
        with get_session(self.url) as session:
            row = session.scalar(select(ForwardTestRun).where(ForwardTestRun.strategy_id == self.strategy_id,
                                                              ForwardTestRun.trade_date == day))
            if row is None:
                # Earlier days still marked running are over now
                session.execute(update(ForwardTestRun).where(
                    ForwardTestRun.strategy_id == self.strategy_id, ForwardTestRun.status.in_(("running", "stopping")),
                    ForwardTestRun.trade_date < day).values(status="stopped", stopped_at=dt.datetime.now(IST)))
                row = ForwardTestRun(strategy_id=self.strategy_id, trade_date=day, status="running",
                                     capital=self.capital, state="waiting",
                                     params={"baseline_net": self.broker.get_funds()["net_pnl"], "done": {},
                                             "kind": self.kind, "name": self.name, "pid": os.getpid()})
                session.add(row)
                session.commit()
            return row.id, row.status, dict(row.params or {})

    # Save what the page shows: status, state, message, P&L and the live view.
    def _save_row(self, run_id: int, **values):
        from marketlens.db import get_session
        from marketlens.models import ForwardTestRun
        with get_session(self.url) as session:
            row = session.get(ForwardTestRun, run_id)
            for key, value in values.items():
                setattr(row, key, value)
            row.heartbeat_at = dt.datetime.now(IST)
            session.commit()

    # ---- one cycle ----

    # One minute of work; returns a small status dict ({"stop": True} when the process should end).
    def cycle(self) -> dict:
        now = self.clock()
        day = now.date()
        run_id, status, params = self._day_row(day)
        self.broker.run_id = run_id
        if status in ("stopping", "stopped"):
            closed = self._square_off("STOPPED")
            message = f"Stopped from the page; {closed} position(s) closed."
            self._save_row(run_id, status="stopped", state="stopped", stopped_at=dt.datetime.now(IST),
                           message=message, pnl=self._today_pnl(params))
            return {"stop": True, "state": "stopped", "message": message}
        if now.weekday() >= 5 or not (ob.MARKET_OPEN < now.time() <= dt.time(15, 31)):
            params["pid"] = os.getpid()
            message = f"Market closed at {now:%a %H:%M}; waiting for 09:16."
            self._save_row(run_id, state="closed", params=params, message=message)
            return {"stop": False, "state": "closed", "message": message}
        try:
            if self.kind == "options":
                state, message, live = self._options_cycle(now, params)
            else:
                state, message, live = self._indicator_cycle(now, params)
        except Exception as err:                       # keep running: next minute may work (e.g. network)
            log.exception("Forward test cycle failed")
            self._save_row(run_id, state="error", message=f"{type(err).__name__}: {err}")
            return {"stop": False, "state": "error", "error": str(err)}
        params.update(live)
        params["market_time"], params["pid"] = f"{now:%H:%M}", os.getpid()
        self._save_row(run_id, state=state, message=message, params=params, pnl=self._today_pnl(params))
        return {"stop": False, "state": state, "message": message}

    # Today's P&L after costs: the paper account now minus where it stood at the start of the day.
    def _today_pnl(self, params: dict) -> float:
        return to_paise(self.broker.get_funds()["net_pnl"] - params.get("baseline_net", 0.0))

    # Close every open paper position of this strategy at the latest price.
    def _square_off(self, reason: str) -> int:
        closed = 0
        for (symbol, product), pos in list(self.broker.positions.items()):
            if pos.is_open:
                side = "SELL" if pos.qty > 0 else "BUY"
                if self.broker.place_order(symbol, side, abs(pos.qty), product=product, tag=reason).filled:
                    closed += 1
        return closed

    # The expiry to trade today, as listed by the exchange (a holiday moves it a day earlier).
    def _expiry(self, day: dt.date) -> dt.date:
        wanted = ob.choose_expiry(self.strategy, day, set())
        listed = [e for e in self.client.get_expiry_list(self.strategy.underlying) if e >= day]
        if not listed or wanted in listed:
            return wanted
        earlier = [e for e in listed if e <= wanted]
        return max(earlier) if earlier else min(listed)

    # Options: snapshot -> candles -> backtester rules up to now -> paper orders for new events.
    def _options_cycle(self, now: dt.datetime, params: dict):
        s, day = self.strategy, now.date()
        if day.weekday() not in s.weekdays:
            return "done", f"Not a trading day for this strategy ({now:%A}).", {}
        if self.minutes is None or self.minutes.day != day:
            expiry = self._expiry(day)
            if s.skip_expiry_day and expiry == day:
                return "done", "Expiry day: skipped by the strategy's setting.", {}
            path = self.data_dir / f"strategy{self.strategy_id}_{day}.csv"
            self.minutes = LiveMinutes(day, expiry, path)
        expiry = self.minutes.expiry
        self.minutes.add_chain(self.client.get_option_chain(s.underlying, expiry), now)
        known = LiveMinutes.candle_of(now)             # candles 0 .. known have finished
        frame = self.minutes.frame()
        if frame.empty or known < 1:
            return "waiting", "Collecting the first prices.", {}
        # Started late: the minutes the rules need (entry candle / the range) were not recorded today
        first = min(m for m, _, _ in self.minutes.bars)
        needed = (ob.minute_index(s.entry_time) - 1 if s.entry_mode == "time" else ob.minute_index(s.range_start))
        if first > max(needed, 0):
            when = s.entry_time if s.entry_mode == "time" else s.range_start
            return "done", (f"Started at {now:%H:%M}, after today's {'entry' if s.entry_mode == 'time' else 'range'} "
                            f"time {when:%H:%M}: the strategy starts trading on the next market day."), {}
        arrays = ob.DayArrays.from_frame(frame, day, expiry)
        run = ob.run_day_until(s, arrays, known + 1)

        done = params.setdefault("done", {})
        fills = params.setdefault("fills", {})
        for event in self._events(run, expiry):
            key = event["key"]
            if key in done:
                continue
            if event["kind"] == "X" and done.get(event["entry_key"]) != "FILLED":
                done[key] = "SKIPPED"                   # its entry never filled (e.g. rejected)
                continue
            price = self.minutes.latest.get((event["strike"], event["type"]))
            if price is None:
                continue                                # no live price yet: try again next minute
            self.broker.update_price(event["symbol"], price)
            result = self.broker.place_order(event["symbol"], event["side"], event["qty"], tag=f"forward:{key}")
            done[key] = result.status
            fills[key] = {"fill": result.avg_price, "engine": event["engine_price"], "status": result.status,
                          "message": result.message}
        for (strike, kind), price in self.minutes.latest.items():
            symbol = f"{s.underlying} {strike:g} {kind} {expiry:%d-%b-%Y}"
            if any(p.symbol == symbol for p in self.broker.positions.values()):
                self.broker.update_price(symbol, price)

        live = self._live_view(run, expiry, fills)
        state, message = self._options_state(run, known + 1, live)
        return state, message, live

    # Every entry and exit the rules have made so far, in time order (exits first at the same minute).
    def _events(self, run, expiry: dt.date) -> list[dict]:
        u = self.strategy.underlying
        events = []

        # One entry or exit as an order to send.
        def add(kind, leg_no, entry_at, when, side, symbol, strike, opt, qty, price, reason=None):
            entry_key = f"E|{leg_no}|{entry_at:%H:%M}"
            events.append({"kind": kind, "key": entry_key if kind == "E" else f"X|{leg_no}|{entry_at:%H:%M}",
                           "entry_key": entry_key, "when": when, "side": side, "symbol": symbol, "strike": strike,
                           "type": opt, "qty": qty, "engine_price": price, "reason": reason})

        for t in run.trades:
            strike, opt = float(t.symbol.split()[1]), t.symbol.split()[2]
            add("E", t.leg_no, t.entry_time, t.entry_time, t.side, t.symbol, strike, opt, t.qty, t.entry_price)
            add("X", t.leg_no, t.entry_time, t.exit_time, "BUY" if t.side == "SELL" else "SELL", t.symbol, strike,
                opt, t.qty, t.exit_price, t.exit_reason)
        for leg in run.legs:
            if leg.status == "open":
                symbol = f"{u} {leg.strike:g} {leg.spec.option_type} {expiry:%d-%b-%Y}"
                add("E", leg.no, leg.entry_at, leg.entry_at, leg.spec.side, symbol, leg.strike, leg.spec.option_type,
                    leg.qty, leg.entry)
        return sorted(events, key=lambda e: (e["when"], e["kind"] != "X"))

    # Open legs and today's trades for the page (engine prices next to the paper fills).
    def _live_view(self, run, expiry: dt.date, fills: dict) -> dict:
        legs, trades = [], []
        for leg in run.legs:
            if leg.status != "open":
                continue
            key = f"E|{leg.no}|{leg.entry_at:%H:%M}"
            fill = fills.get(key, {}).get("fill")
            ltp = self.minutes.latest.get((leg.strike, leg.spec.option_type))
            sign = 1 if leg.spec.side == "BUY" else -1
            legs.append({"leg": leg.no, "symbol": f"{self.strategy.underlying} {leg.strike:g} {leg.spec.option_type}",
                         "side": leg.spec.side, "qty": leg.qty, "entry_time": f"{leg.entry_at:%H:%M}",
                         "paper_entry": fill, "rule_entry": leg.entry, "ltp": ltp,
                         "sl": round(leg.sl, 2) if leg.sl is not None else None,
                         "target": round(leg.target, 2) if leg.target is not None else None,
                         "pnl": to_paise(sign * leg.qty * (ltp - fill)) if fill and ltp else None})
        for t in run.trades:
            entry = fills.get(f"E|{t.leg_no}|{t.entry_time:%H:%M}", {}).get("fill")
            exit_ = fills.get(f"X|{t.leg_no}|{t.entry_time:%H:%M}", {}).get("fill")
            sign = 1 if t.side == "BUY" else -1
            trades.append({"leg": t.leg_no, "symbol": t.symbol, "side": t.side, "qty": t.qty,
                           "entry_time": f"{t.entry_time:%H:%M}", "exit_time": f"{t.exit_time:%H:%M}",
                           "paper_entry": entry, "paper_exit": exit_, "rule_entry": t.entry_price,
                           "rule_exit": t.exit_price, "exit_reason": t.exit_reason, "note": t.note,
                           "paper_pnl": to_paise(sign * t.qty * (exit_ - entry)) if entry and exit_ else None,
                           "rule_pnl": t.gross_pnl})
        return {"legs": legs, "trades": trades}

    # The state words the page shows.
    def _options_state(self, run, now: int, live: dict) -> tuple[str, str]:
        s = self.strategy
        if now > run.last:
            return "done", f"Exit time {s.exit_time:%H:%M} passed: {len(live['trades'])} trade(s) today."
        if live["legs"]:
            return "in_trade", f"{len(live['legs'])} leg(s) open."
        if run.first_entry is None:
            if s.entry_mode == "breakout":
                return "watching", f"Range {s.range_start:%H:%M}-{s.range_end:%H:%M}: waiting for the breakout."
            return "waiting", f"Entry at {s.entry_time:%H:%M}."
        waiting = [leg for leg in run.legs if leg.status in ("scheduled", "cost", "breakout")]
        if waiting:
            return "in_trade", f"All legs closed; {len(waiting)} waiting to re-enter."
        return "done", f"All legs closed: {len(live['trades'])} trade(s) today."

    # Indicator strategy: signal on the last finished candle, square off at the exit time.
    def _indicator_cycle(self, now: dt.datetime, params: dict):
        s, day = self.strategy, now.date()
        candles = self.client.get_intraday(s.symbol, day, day, s.timeframe)
        ts = pd.to_datetime(candles["timestamp"])
        finished = candles[(ts + pd.Timedelta(minutes=s.timeframe)) <= now]   # never an unfinished candle
        held = self.broker.positions.get((s.symbol, s.product))
        qty = held.qty if held else 0
        trades = params.setdefault("trades", [])
        if finished.empty:
            return "waiting", "Waiting for the first finished candle.", {"legs": [], "trades": trades}
        price = float(finished["close"].iloc[-1])
        self.broker.update_price(s.symbol, price)
        last_ts = f"{pd.Timestamp(finished['timestamp'].iloc[-1]):%H:%M}"

        # Send one order and remember it for the page.
        def order(side, n, reason):
            result = self.broker.place_order(s.symbol, side, n, product=s.product, tag=f"forward:{reason}")
            trades.append({"time": f"{now:%H:%M}", "candle": last_ts, "side": side, "qty": n,
                           "paper_fill": result.avg_price, "status": result.status, "exit_reason": reason})

        if now.time() >= s.exit_time:
            if qty:
                order("SELL" if qty > 0 else "BUY", abs(qty), "TIME")
            state, message = "done", f"Exit time {s.exit_time:%H:%M} passed."
        elif now.time() < s.start_time:
            state, message = "waiting", f"Signals start at {s.start_time:%H:%M}."
        else:
            seen = params.setdefault("done", {})
            signal = None if last_ts in seen else s.signal(finished["close"].astype(float).reset_index(drop=True))
            seen[last_ts] = signal or "-"
            if signal == "BUY" and qty <= 0:
                order("BUY", s.qty + abs(qty), "SIGNAL")
            elif signal == "SELL" and qty > 0:
                order("SELL", qty + (s.qty if s.allow_short else 0), "SIGNAL")
            elif signal == "SELL" and qty == 0 and s.allow_short:
                order("SELL", s.qty, "SIGNAL")
            held = self.broker.positions.get((s.symbol, s.product))
            state = "in_trade" if held and held.is_open else "waiting"
            message = f"Last candle {last_ts}: {signal or 'no signal'}; price {price:,.2f}."
        held = self.broker.positions.get((s.symbol, s.product))
        legs = ([{"symbol": s.symbol, "qty": held.qty, "avg_price": held.avg_price, "ltp": held.ltp,
                  "pnl": held.unrealized}] if held and held.is_open else [])
        return state, message, {"legs": legs, "trades": trades}


# ---------------------------------------------------------------- for the page

# True when a process with this id is running on this computer (Windows and Ubuntu).
def pid_alive(pid) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # "query limited information"
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259                                               # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)                                                   # signal 0 = only check
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# Today's (or the latest) status of a strategy's forward test, for the page.
def status(strategy_id: int, url: str | None = None) -> dict | None:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import ForwardTestRun
    with get_session(url) as session:
        row = session.scalar(select(ForwardTestRun).where(ForwardTestRun.strategy_id == strategy_id)
                             .order_by(ForwardTestRun.trade_date.desc().nulls_last(), ForwardTestRun.id.desc()))
        if row is None:
            return None
        age = (dt.datetime.now(IST) - row.heartbeat_at).total_seconds() if row.heartbeat_at else None
        pid = (row.params or {}).get("pid")
        fresh = age is not None and age < ALIVE_SECONDS and row.status == "running"
        return {"run_id": row.id, "day": row.trade_date, "status": row.status, "state": row.state,
                "state_text": STATES.get(row.state, row.state or "-"), "message": row.message,
                "pnl": float(row.pnl) if row.pnl is not None else 0.0, "params": dict(row.params or {}),
                "heartbeat_age": age, "alive": fresh and (pid_alive(pid) or pid == os.getpid())}


# Start the background process for a strategy (it keeps running when the browser is closed).
def start_process(strategy_id: int, capital: float | None = None) -> int:
    LOG_DIR.mkdir(exist_ok=True)
    command = [sys.executable, "-m", "marketlens.trading.forward", "--strategy", str(strategy_id)]
    if capital:
        command += ["--capital", str(capital)]
    log_file = (LOG_DIR / f"forward_{strategy_id}.log").open("a")
    options = {"cwd": PROJECT_ROOT, "stdout": log_file, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":                               # Windows: its own process, no console window
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:                                             # Ubuntu: not stopped when the app stops
        options["start_new_session"] = True
    return subprocess.Popen(command, **options).pid


# Ask the background process to stop (it closes the positions on its next cycle).
def request_stop(strategy_id: int, url: str | None = None) -> bool:
    from sqlalchemy import select, update

    from marketlens.db import get_session
    from marketlens.models import ForwardTestRun
    with get_session(url) as session:
        latest = session.scalar(select(ForwardTestRun.id).where(ForwardTestRun.strategy_id == strategy_id,
                                                                ForwardTestRun.status == "running")
                                .order_by(ForwardTestRun.id.desc()))
        if latest is None:
            return False
        session.execute(update(ForwardTestRun).where(ForwardTestRun.id == latest).values(status="stopping"))
        session.commit()
        return True


# Day-by-day forward-test P&L next to the backtest of the same days (when their option data is stored).
def history(strategy_id: int, url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import ForwardTestRun
    with get_session(url) as session:
        rows = session.scalars(select(ForwardTestRun).where(ForwardTestRun.strategy_id == strategy_id,
                                                            ForwardTestRun.trade_date.is_not(None))
                               .order_by(ForwardTestRun.trade_date)).all()
        days = [{"date": r.trade_date, "forward_pnl": float(r.pnl or 0), "state": r.state,
                 "trades": len((r.params or {}).get("trades", [])), "slippage": _slippage(r.params or {})} for r in rows]
    table = pd.DataFrame(days, columns=["date", "forward_pnl", "state", "trades", "slippage"])
    table["backtest_pnl"] = None
    if table.empty:
        return table.assign(difference=None)
    kind, _, strategy = load_strategy(strategy_id, url)
    if kind == "options":
        try:
            settings = ob.settings_from_params({**ob.OptionsBacktestSettings.to_params(strategy),
                                                "start": table["date"].min().isoformat(),
                                                "end": table["date"].max().isoformat()})
            result = ob.run_options_backtest(settings, save=False, url=url)
            table["backtest_pnl"] = table["date"].map(result.daily_pnl.to_dict())
            traded = set(result.extras["mtm"]["date"]) if len(result.extras["mtm"]) else set()
            table.loc[table["backtest_pnl"].isna() & table["date"].isin(traded), "backtest_pnl"] = 0.0
        except Exception as err:
            log.warning("Backtest for the forward-test days failed: %s", err)
    table["difference"] = table["forward_pnl"] - pd.to_numeric(table["backtest_pnl"], errors="coerce")
    return table


# Average paper fill minus rule price per order (positive = paid more / received less than the rule).
def _slippage(params: dict):
    gaps = []
    for key, f in (params.get("fills") or {}).items():
        if f.get("fill") is None or f.get("engine") is None:
            continue
        gaps.append(abs(f["fill"] - f["engine"]))
    return round(sum(gaps) / len(gaps), 2) if gaps else None


# ---------------------------------------------------------------- the background process

# Seconds until a few seconds past the next whole minute (when the last candle has just finished).
def _seconds_to_next_minute(now: dt.datetime) -> float:
    nxt = (now + dt.timedelta(minutes=1)).replace(second=3, microsecond=0)
    return max((nxt - now).total_seconds(), 1.0)


# Command line: python -m marketlens.trading.forward --strategy ID [--capital X] [--once] [--at TIME]
def main(argv: list[str] | None = None, url: str | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(description="Forward-test a saved strategy with virtual money.")
    parser.add_argument("--strategy", required=True,
                        help="Strategy Library id (shown on the Forward Test page), or 'demo' for the practice strategy")
    parser.add_argument("--capital", type=float, default=None, help="virtual capital (default: PAPER_CAPITAL)")
    parser.add_argument("--once", action="store_true", help="run a single cycle and stop (for testing)")
    parser.add_argument("--at", default=None, help='DEMO only: pretend it is this time, e.g. "2026-10-28 09:25"')
    args = parser.parse_args(argv)
    try:
        args.strategy = resolve_strategy(args.strategy, url)
    except ValueError as err:
        print(err)
        return 2
    if config.TRADING_MODE == "live":
        log.warning("TRADING_MODE=live, but forward tests always use the paper broker (virtual money).")
    clock = None
    if args.at:
        fixed = dt.datetime.fromisoformat(args.at).replace(tzinfo=IST)
        clock = lambda: fixed  # noqa: E731  (one fixed moment)
        client = client or live_client(now=fixed)
    current = status(args.strategy, url)
    if not args.once and current and current["alive"] and current["params"].get("pid") != os.getpid():
        print(f"Strategy {args.strategy} is already running (pid {current['params'].get('pid')}).")
        return 1
    runner = ForwardRunner(args.strategy, args.capital, client=client, url=url, clock=clock)
    print(f"Forward test of '{runner.name}' (strategy id {args.strategy}, {runner.kind}) started. "
          "Press Ctrl+C to stop.")
    while True:
        result = runner.cycle()
        print(f"{dt.datetime.now(IST):%H:%M:%S} {result.get('state')}: {result.get('message', result.get('error', ''))}",
              flush=True)
        if result.get("stop") or args.once:
            return 0
        try:
            time.sleep(_seconds_to_next_minute(dt.datetime.now(IST)))
        except KeyboardInterrupt:
            print("Stopped with Ctrl+C (positions stay open; press Stop on the page to close them).")
            return 0


if __name__ == "__main__":
    sys.exit(main())
