"""Momentum ranking of stocks (module 8): score, filter and rank on any past date.

    from marketlens.momentum.ranking import MomentumSettings, load_ranker
    ranker = load_ranker(symbols, date(2026, 10, 30))
    table = ranker.rank(date(2026, 10, 30))      # symbol, score, rank, passed, reason
    ranker.picks(date(2026, 10, 30), top_n=10)   # what to hold: the top 10, or LIQUIDBEES / cash

No look-ahead: everything for a date is worked out from the prices up to and including that
date's close. Prices after it are cut off first, so they cannot change the answer.

Score = weighted average over the lookbacks (63, 126, 252 trading days = 3, 6, 12 months),
each measured up to `skip_recent` days ago (the last month is skipped because very recent winners
often fall back for a while). Methods:
- return:       the % change over the lookback.
- vol_adjusted: that change divided by the yearly volatility of daily moves (calm risers first).
- sharpe:       yearly average daily return divided by yearly volatility.
"""
import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from marketlens.logger import get_logger

log = get_logger(__name__)

METHODS = ("return", "vol_adjusted", "sharpe")
TRADING_DAYS = 252
NIFTY_INDEX = "^NSEI"             # Nifty 50 in the daily_prices table (synced from yfinance)
SAFE_ASSETS = ("LIQUIDBEES", "CASH")
CRORE = 1e7
FILL_LIMIT = 5                    # a stock missing a few days keeps its last close (past prices only)


# Settings of one ranking (also saved with a momentum strategy).
@dataclass
class MomentumSettings:
    lookbacks: list = field(default_factory=lambda: [63, 126, 252])   # trading days
    weights: list = field(default_factory=lambda: [1, 1, 1])
    method: str = "return"
    skip_recent: int = 21             # leave out the last month
    ma_days: int = 200                # price must be above its 200-day average; 0 = off
    min_traded_value_cr: float = 0.0  # average daily close x volume, Rs crore; 0 = off
    value_days: int = 20              # days averaged for the traded value
    max_one_day_move_pct: float = 0.0 # skip stocks that moved more than this in one day; 0 = off
    move_days: int = 252              # days checked for that big move
    index_filter: bool = True         # buy only when Nifty 50 is above its 200-day average
    index_ma_days: int = 200
    safe_asset: str = "LIQUIDBEES"    # held when the index filter says no: LIQUIDBEES or CASH

    # Stop with a clear message when a setting cannot work.
    def check(self):
        if self.method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}, not {self.method!r}")
        if not self.lookbacks or len(self.lookbacks) != len(self.weights):
            raise ValueError("give one weight for each lookback")
        if any(int(n) != n or n < 1 for n in self.lookbacks):
            raise ValueError("lookbacks must be whole numbers of days, 1 or more")
        if any(w < 0 for w in self.weights) or sum(self.weights) <= 0:
            raise ValueError("weights must be 0 or more and not all 0")
        if self.method != "return" and min(self.lookbacks) < 2:
            raise ValueError("vol_adjusted and sharpe need lookbacks of at least 2 days")
        if self.skip_recent < 0:
            raise ValueError("skip_recent cannot be negative")
        if min(self.ma_days, self.value_days, self.move_days, self.index_ma_days) < 0:
            raise ValueError("day counts cannot be negative")
        if self.safe_asset not in SAFE_ASSETS:
            raise ValueError(f"safe_asset must be one of {SAFE_ASSETS}")


# The rows up to and including `date` (later prices are dropped before anything is worked out).
def up_to(frame: pd.DataFrame, date) -> pd.DataFrame:
    return frame.loc[:pd.Timestamp(date)].sort_index()


# Momentum score of every column (symbol) on `date`, from close prices (rows = dates).
def momentum_scores(close_prices: pd.DataFrame, date, lookbacks=(63, 126, 252), weights=(1, 1, 1),
                    method: str = "return", skip_recent: int = 21) -> pd.Series:
    """NaN for a symbol without enough history for every lookback."""
    MomentumSettings(list(lookbacks), list(weights), method, skip_recent).check()
    closes = up_to(close_prices, date).ffill(limit=FILL_LIMIT)
    end = len(closes) - 1 - skip_recent                   # the row the lookbacks end on
    total = pd.Series(0.0, index=closes.columns)
    for days, weight in zip(lookbacks, weights):
        if end - days < 0:
            return pd.Series(np.nan, index=closes.columns, name="score")
        window = closes.iloc[end - days:end + 1]
        change = window.iloc[-1] / window.iloc[0] - 1
        if method == "return":
            part = change
        else:
            daily = window.pct_change(fill_method=None).iloc[1:]
            vol = daily.std() * math.sqrt(TRADING_DAYS)
            part = change / vol if method == "vol_adjusted" else daily.mean() * TRADING_DAYS / vol
        total += weight * part
    return (total / sum(weights)).replace([np.inf, -np.inf], np.nan).rename("score")


# Is the last close above the average of the last `days` closes? NaN-safe: too little history = False.
def above_average(closes: pd.DataFrame, days: int) -> pd.Series:
    if days == 0:
        return pd.Series(True, index=closes.columns)
    if len(closes) < days:
        return pd.Series(False, index=closes.columns)
    recent = closes.iloc[-days:]
    return (recent.iloc[-1] > recent.mean()) & recent.notna().all()


# Average daily traded value (close x volume) over the last `days` rows, in Rs crore.
def traded_value_cr(closes: pd.DataFrame, volumes: pd.DataFrame, days: int) -> pd.Series:
    value = (closes * volumes.reindex(index=closes.index, columns=closes.columns)).iloc[-days:]
    return value.mean() / CRORE


# Biggest one-day move (up or down) over the last `days` rows, in %.
def biggest_move_pct(closes: pd.DataFrame, days: int) -> pd.Series:
    moves = closes.pct_change(fill_method=None).iloc[-days:].abs() * 100
    return moves.max()


class MomentumRanker:
    """Holds the price history and ranks the symbols on any date (using only prices up to it).

    close:       rows = dates, columns = symbols (load_prices layout)
    volume:      same layout; needed only for the traded value filter
    index_close: Nifty 50 closes (a Series); needed only for the index filter
    """

    # Keep the prices and check the settings once.
    def __init__(self, close: pd.DataFrame, volume: pd.DataFrame | None = None,
                 index_close: pd.Series | None = None, settings: MomentumSettings | None = None):
        self.settings = settings or MomentumSettings()
        self.settings.check()
        if self.settings.min_traded_value_cr > 0 and volume is None:
            raise ValueError("the traded value filter needs volumes (or set min_traded_value_cr to 0)")
        if self.settings.index_filter and index_close is None:
            raise ValueError(f"the index filter needs Nifty 50 prices ({NIFTY_INDEX}): "
                             "sync them or switch the index filter off")
        self.close = close.sort_index()
        self.volume = volume
        self.index_close = index_close.sort_index().dropna() if index_close is not None else None

    # Is Nifty 50 above its average on `date`? Returns (ok, last close, average).
    def market_ok(self, date) -> tuple[bool, float, float]:
        s = self.settings
        if not s.index_filter:
            return True, math.nan, math.nan
        index = up_to(self.index_close.to_frame("index"), date)["index"]
        if len(index) < s.index_ma_days:
            return False, (float(index.iloc[-1]) if len(index) else math.nan), math.nan
        average = float(index.iloc[-s.index_ma_days:].mean())
        return bool(index.iloc[-1] > average), float(index.iloc[-1]), average

    # Score, rank and filters for every symbol on `date`.
    def rank(self, date) -> pd.DataFrame:
        """Columns: symbol, score, rank (1 = best of those that passed), passed, reason.

        attrs: as_of (last trading day used), market_ok, index_close, index_average, hold.
        """
        s = self.settings
        closes = up_to(self.close, date).ffill(limit=FILL_LIMIT)
        if closes.empty:
            raise ValueError(f"no prices on or before {pd.Timestamp(date).date()}")
        scores = momentum_scores(closes, closes.index[-1], s.lookbacks, s.weights, s.method, s.skip_recent)
        reasons = pd.Series("", index=closes.columns)

        # Add a reason to every symbol where `failed` is True (only the first reason is kept).
        def fail(failed: pd.Series, text):
            for sym in failed[failed].index:
                if not reasons[sym]:
                    reasons[sym] = text if isinstance(text, str) else text(sym)

        fail(scores.isna(), "not enough price history")
        fail(~above_average(closes, s.ma_days), f"price not above its {s.ma_days}-day average")
        if s.min_traded_value_cr > 0:
            value = traded_value_cr(closes, up_to(self.volume, date), s.value_days)
            fail(~(value >= s.min_traded_value_cr),
                 lambda sym: f"traded value Rs {value[sym]:.1f} cr < Rs {s.min_traded_value_cr:g} cr")
        if s.max_one_day_move_pct > 0:
            move = biggest_move_pct(closes, s.move_days)
            fail(move > s.max_one_day_move_pct,
                 lambda sym: f"one-day move of {move[sym]:.1f}% > {s.max_one_day_move_pct:g}%")

        table = pd.DataFrame({"symbol": closes.columns, "score": scores.values,
                              "passed": (reasons == "").values, "reason": reasons.values})
        table = table.sort_values(["passed", "score"], ascending=[False, False], na_position="last")
        table.insert(2, "rank", pd.array([n + 1 if ok else pd.NA for n, ok in enumerate(table["passed"])],
                                         dtype="Int64"))
        table = table.reset_index(drop=True)
        ok, index_close, index_average = self.market_ok(closes.index[-1])
        table.attrs.update(as_of=closes.index[-1].date(), market_ok=ok, index_close=index_close,
                           index_average=index_average, hold=None if ok else s.safe_asset)
        return table

    # What to hold on `date`: the top N that passed, or [LIQUIDBEES] / [] (cash) when the index is weak.
    def picks(self, date, top_n: int = 10) -> list[str]:
        table = self.rank(date)
        if not table.attrs["market_ok"]:
            return [] if self.settings.safe_asset == "CASH" else [self.settings.safe_asset]
        return table[table["passed"]].head(top_n)["symbol"].tolist()


# Calendar days of history needed before `date` for these settings (with room for holidays).
def history_days(settings: MomentumSettings) -> int:
    rows = max(max(settings.lookbacks) + settings.skip_recent, settings.ma_days, settings.value_days,
               settings.move_days, settings.index_ma_days if settings.index_filter else 0) + 1
    return int(rows * 365 / 240) + 30


# Build a ranker from the prices stored in PostgreSQL (synced with sync_prices), up to `date` only.
def load_ranker(symbols: list[str], date: dt.date, settings: MomentumSettings | None = None,
                url: str | None = None) -> MomentumRanker:
    from marketlens.ingest.prices import load_prices
    settings = settings or MomentumSettings()
    settings.check()
    start = date - dt.timedelta(days=history_days(settings))
    close = load_prices(symbols, start, date, url=url)
    if close.empty:
        raise ValueError("no stored prices for these symbols: sync them first (Data Downloader page)")
    volume = load_prices(symbols, start, date, field_name="volume", url=url) \
        if settings.min_traded_value_cr > 0 else None
    index_close = None
    if settings.index_filter:
        index = load_prices([NIFTY_INDEX], start, date, url=url)
        if index.empty:
            raise ValueError(f"no stored Nifty 50 prices ({NIFTY_INDEX}): sync them or switch the index filter off")
        index_close = index[NIFTY_INDEX]
    log.info("Momentum ranker: %d symbols, %s to %s", close.shape[1], start, date)
    return MomentumRanker(close, volume, index_close, settings)
