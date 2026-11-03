"""Stock momentum portfolio backtest (module 8): hold the top N Nifty 50 / Nifty 200 stocks against Nifty 50.

    settings = StockMomentumSettings(universe="nifty200", top_n=20, hold_rank=30)
    result = run_momentum_backtest(settings, progress=lambda pct, msg: print(pct, msg))

How one rebalance works (no look-ahead):
1. Signal: at the close of the last trading day of the month (or week), ranking.py's MomentumRanker ranks
   every stock using only prices up to that close (scores, 200-day average, liquidity and index filters).
2. Choose: stocks already held stay while they rank in the top M ("hold while in top M" buffer, which cuts
   trading); empty places go to the best-ranked stocks not held, until there are N.
3. Weigh: equal weights, or inverse volatility (calmer stocks get more money; weights add up to 1).
4. Trade: at the close of the NEXT trading day, with costs.py equity-delivery charges, slippage and capital
   gains tax (portfolio.py, shared with the ETF backtest of module 9).
When Nifty 50 is below its 200-day average (index filter) everything goes to LIQUIDBEES or cash.

Prices come from PostgreSQL (load_prices); missing ones are listed so you can run sync_prices() first.
Note: the universe lists are today's index members, so stocks that left the index in the past are missing
(survivorship bias); real results would usually be a little lower.
Historical analysis for learning, not a live trading recommendation.
"""
import datetime as dt
import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from marketlens.backtest.engine import BacktestResult, rebase
from marketlens.backtest.metrics import compute_metrics
from marketlens.costs import CostSettings, to_paise
from marketlens.logger import get_logger
from marketlens.momentum.portfolio import (Portfolio, TaxSettings, curve_metrics, liquidation_value,
                                           rebalance_days, rebalance_to, yearly_returns)
from marketlens.momentum.ranking import NIFTY_INDEX, MomentumRanker, MomentumSettings, history_days

log = get_logger(__name__)

UNIVERSES = ("nifty50", "nifty200")
REBALANCE = ("monthly", "weekly")
WEIGHTINGS = ("equal", "inverse_vol")
LIQUID = "LIQUIDBEES"
LIQUID_START_PRICE = 1000.0
NIFTY_NAME = "Nifty 50 buy-and-hold (^NSEI, without dividends)"
# ETFs that follow the Nifty 200 Momentum 30 index (Yahoo has no history of the index itself): the first stored one is used
MOMENTUM_TRACKERS = {"MOMENTUM": "Nifty 200 Momentum 30 (MOMENTUM ETF)",
                     "MOM30IETF": "Nifty 200 Momentum 30 (MOM30IETF ETF)"}
STALE_DAYS = 7                      # stored prices older than this before the end date count as missing
NOTE = "Historical analysis for learning, not a live trading recommendation."
SURVIVORSHIP_NOTE = ("The stock lists are today's index members, so stocks that left the index in the past are "
                     "missing (survivorship bias): real results would usually have been a little lower.")


@dataclass
class StockMomentumSettings:
    """Everything one stock momentum backtest needs."""
    universe: str = "nifty200"            # nifty50 or nifty200
    start: dt.date = dt.date(2016, 1, 1)
    end: dt.date | None = None            # None = the last stored day
    top_n: int = 20                       # stocks held
    hold_rank: int = 30                   # buffer: a held stock stays while its rank is M or better (M = N: no buffer)
    rebalance: str = "monthly"            # monthly or weekly
    weighting: str = "equal"              # equal or inverse_vol
    vol_days: int = 63                    # days of daily moves used for inverse-volatility weights
    drift_band_pct: float = 10.0          # re-size a kept stock only when it is this % away from its weight
    capital: float = 1_000_000.0
    liquid_yield_pct: float = 5.0         # LIQUIDBEES (safe asset) grows at this yearly rate
    costs: CostSettings = field(default_factory=lambda: CostSettings(slippage_value=0.05))   # equity delivery
    tax: TaxSettings = field(default_factory=TaxSettings)
    filters: MomentumSettings = field(default_factory=MomentumSettings)      # ranking.py: scores and filters

    # Stop with a clear message when a setting cannot work.
    def check(self):
        if self.universe not in UNIVERSES:
            raise ValueError(f"universe must be one of {UNIVERSES}")
        size = 50 if self.universe == "nifty50" else 200
        if not 1 <= self.top_n <= min(50, size):
            raise ValueError(f"top N must be 1 to {min(50, size)}")
        if not self.top_n <= self.hold_rank <= size:
            raise ValueError(f"'hold while in top M' must be from top N ({self.top_n}) to {size}")
        if self.rebalance not in REBALANCE:
            raise ValueError(f"rebalance must be one of {REBALANCE}")
        if self.weighting not in WEIGHTINGS:
            raise ValueError(f"weighting must be one of {WEIGHTINGS}")
        if self.vol_days < 5:
            raise ValueError("volatility days must be 5 or more")
        if self.capital < 10_000:
            raise ValueError("capital must be at least Rs 10,000")
        if self.drift_band_pct < 0 or self.liquid_yield_pct < 0:
            raise ValueError("the drift band and the LIQUIDBEES yield cannot be negative")
        if self.end is not None and self.end <= self.start:
            raise ValueError("end date must be after the start date")
        self.filters.check()
        self.tax.check()

    # Plain values for the Strategy Library, the database and the API.
    def to_params(self) -> dict:
        values = asdict(self)
        values["start"] = self.start.isoformat()
        values["end"] = self.end.isoformat() if self.end else None
        return values

    # Settings back from to_params() (or from the web form: missing values keep their defaults).
    @classmethod
    def from_params(cls, params: dict) -> "StockMomentumSettings":
        values = {k: v for k, v in (params or {}).items() if k in cls.__dataclass_fields__}
        for name in ("start", "end"):
            if isinstance(values.get(name), str):
                values[name] = dt.date.fromisoformat(values[name]) if values[name] else None
        if values.get("start") is None:
            values.pop("start", None)
        try:
            values["costs"] = CostSettings(**values.get("costs", {}))
            values["tax"] = TaxSettings(**values.get("tax", {}))
            values["filters"] = MomentumSettings(**values.get("filters", {}))
            return cls(**values)
        except TypeError as err:                       # an unknown setting name
            raise ValueError(f"Unknown setting: {err}") from None

    # Today's index members (data/universe/<universe>.csv).
    def symbols(self) -> list[str]:
        return universe_table(self.universe)["symbol"].tolist()

    # The first day of prices needed: the start minus the longest lookback / average (with room for holidays).
    def history_start(self) -> dt.date:
        rows = max(history_days(self.filters), int((self.vol_days + 1) * 365 / 240) + 30)
        return self.start - dt.timedelta(days=rows)


# Symbol, name and sector of the universe.
def universe_table(universe: str) -> pd.DataFrame:
    from marketlens.ingest.prices import load_universe
    return load_universe(universe)


# ---------------------------------------------------------------- price data

@dataclass
class StockData:
    """Prices for one backtest: rows = dates, columns = symbols."""
    close: pd.DataFrame
    volume: pd.DataFrame | None = None
    index_close: pd.Series | None = None          # Nifty 50 (benchmark and index filter)
    trackers: dict = field(default_factory=dict)   # Nifty 200 Momentum 30 ETF name -> closes
    is_demo: bool = False


class MissingPricesError(ValueError):
    """Prices the backtest needs are not stored; .problems lists them (run sync_prices() first)."""

    def __init__(self, problems: list[dict]):
        self.problems = problems
        shown = "; ".join(f"{p['symbol']} ({p['problem']})" for p in problems[:10])
        more = f" and {len(problems) - 10} more" if len(problems) > 10 else ""
        super().__init__(f"Prices are missing for {len(problems)} symbol(s): {shown}{more}. "
                         "Download them first with sync_prices() (the 'Download missing prices' button), "
                         "or run anyway without them.")


# First and last stored date of each symbol (one quick query).
def stored_ranges(symbols: list[str], url: str | None = None) -> dict:
    from sqlalchemy import func, select

    from marketlens.db import get_session
    from marketlens.models import DailyPrice, Instrument
    with get_session(url) as session:
        rows = session.execute(
            select(Instrument.symbol, func.min(DailyPrice.date), func.max(DailyPrice.date))
            .join(DailyPrice, DailyPrice.instrument_id == Instrument.id)
            .where(Instrument.symbol.in_(symbols)).group_by(Instrument.symbol)).all()
    return {sym: (first, last) for sym, first, last in rows}


# Which prices are missing for these settings: none stored, or not up to the end date.
def price_problems(settings: StockMomentumSettings, url: str | None = None, today: dt.date | None = None) -> list[dict]:
    """[{symbol, problem, stored_from, stored_to, needed_from, needed_to}]. A stock stored from a later day than
    needed is fine (it was listed later and is ranked once it has enough history)."""
    needed_from = settings.history_start()
    needed_to = settings.end or (today or dt.date.today())
    wanted = settings.symbols() + ([NIFTY_INDEX])
    ranges = stored_ranges(wanted, url)
    problems = []
    for sym in wanted:
        first, last = ranges.get(sym, (None, None))
        if first is None:
            problem = f"nothing stored, need {needed_from:%d-%b-%Y} to {needed_to:%d-%b-%Y}"
        elif last < needed_to - dt.timedelta(days=STALE_DAYS):
            problem = f"stored only up to {last:%d-%b-%Y}, need up to {needed_to:%d-%b-%Y}"
        else:
            continue
        problems.append({"symbol": sym, "problem": problem, "stored_from": first, "stored_to": last,
                         "needed_from": needed_from, "needed_to": needed_to})
    return problems


# A warning when most stocks are stored from a later day than the ranking needs (sync_prices cannot add
# older history), with the earliest start date that works; None when the history is long enough.
def history_warning(settings: StockMomentumSettings, url: str | None = None) -> str | None:
    needed_from = settings.history_start()
    firsts = sorted(first for first, _ in stored_ranges(settings.symbols(), url).values())
    if not firsts:
        return None
    typical = firsts[len(firsts) // 2]                       # half the stocks are stored from this day or earlier
    if typical <= needed_from + dt.timedelta(days=30):
        return None
    earliest = settings.start + (typical - needed_from)
    return (f"Most stocks are stored only from {typical:%d-%b-%Y}, but a start on {settings.start:%d-%b-%Y} needs "
            f"prices from {needed_from:%d-%b-%Y} for the longest lookback and averages. Until about "
            f"{earliest:%d-%b-%Y} few stocks can be ranked, so the money waits in the safe asset. Choose a start "
            f"of {earliest:%d-%b-%Y} or later for a fair test.")


# Read the prices from PostgreSQL. Raises MissingPricesError unless allow_missing.
def load_stock_data(settings: StockMomentumSettings, url: str | None = None, allow_missing: bool = False) -> StockData:
    from marketlens.ingest.prices import load_prices
    problems = price_problems(settings, url)
    if problems and not allow_missing:
        raise MissingPricesError(problems)
    start, end = settings.history_start(), settings.end or dt.date.today()
    close = load_prices(settings.symbols(), start, end, url=url)
    if close.empty:
        raise MissingPricesError(problems or [{"symbol": settings.universe, "problem": "nothing stored"}])
    volume = load_prices(settings.symbols(), start, end, field_name="volume", url=url) \
        if settings.filters.min_traded_value_cr > 0 else None
    index = load_prices([NIFTY_INDEX], start, end, url=url)
    if index.empty and settings.filters.index_filter:
        raise MissingPricesError([{"symbol": NIFTY_INDEX, "problem": "nothing stored (needed for the index filter)"}])
    trackers = load_prices(list(MOMENTUM_TRACKERS), settings.start, end, url=url)
    return StockData(close, volume, index[NIFTY_INDEX] if not index.empty else None,
                     {MOMENTUM_TRACKERS[s]: trackers[s] for s in trackers.columns})


# Download what is missing (yfinance -> daily_prices): stocks, Nifty 50 and the Momentum 30 ETFs.
def sync_universe_prices(settings: StockMomentumSettings, progress=None, url: str | None = None) -> dict:
    """progress(percent, message) like a background job's. Returns a short summary."""
    from marketlens.ingest.prices import sync_prices
    start, end = settings.history_start(), settings.end or dt.date.today()
    universe = universe_table(settings.universe)
    steps = [("stocks", settings.symbols(), "EQUITY", dict(zip(universe["symbol"], universe["name"]))),
             ("Nifty 50", [NIFTY_INDEX], "INDEX", {NIFTY_INDEX: "Nifty 50"}),
             ("Momentum 30 ETFs", list(MOMENTUM_TRACKERS), "ETF", MOMENTUM_TRACKERS)]
    summary = {"rows": 0, "failed": [], "up_to_date": 0}
    for n, (label, symbols, kind, names) in enumerate(steps):
        base = 100 * n / len(steps)

        # Pass the sync's own progress on as a share of this step.
        def step(done, total, text, base=base, label=label):
            if progress:
                progress(base + 100 / len(steps) * done / max(total, 1), f"Downloading {label}: {text}")
        if progress:
            progress(base, f"Downloading {label}")
        result = sync_prices(symbols, start, end, instrument_type=kind, names=names, url=url, progress=step)
        summary["rows"] += result.rows_upserted
        summary["failed"] += list(getattr(result, "failed", []) or [])
        summary["up_to_date"] += len(result.up_to_date)
    return summary


# Made-up prices for tests and quick tries (labelled DEMO): `n` stocks with different trends, plus Nifty 50.
def demo_stock_data(n: int = 30, start: dt.date = dt.date(2015, 1, 1), end: dt.date = dt.date(2026, 10, 30),
                    seed: int = 7) -> StockData:
    days = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    drift = rng.uniform(-0.0002, 0.0009, n)
    vol = rng.uniform(0.008, 0.025, n)
    moves = rng.normal(drift, vol, (len(days), n))
    close = pd.DataFrame(100 * np.cumprod(1 + moves, axis=0), index=days,
                         columns=[f"STOCK{i:03d}" for i in range(n)]).round(2)
    index = pd.Series(10_000 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(days))), index=days, name=NIFTY_INDEX)
    return StockData(close, None, index, {}, is_demo=True)


# ---------------------------------------------------------------- choosing and weighing

# The stocks to hold after a signal: held ones still in the top M first, then the best others up to N.
def choose(table: pd.DataFrame, held: list[str], top_n: int, hold_rank: int) -> list[str]:
    """table: MomentumRanker.rank() output (rank 1 = best of those that passed the filters)."""
    ranks = table.dropna(subset=["rank"]).set_index("symbol")["rank"].astype(int)
    kept = sorted((s for s in held if s in ranks.index and ranks[s] <= hold_rank), key=ranks.get)[:top_n]
    fresh = [s for s in ranks.sort_values().index if s not in kept][:top_n - len(kept)]
    return kept + fresh


# Weights of the chosen stocks that add up to 1: equal, or 1 / volatility (calmer stocks get more).
def stock_weights(chosen: list[str], how: str, vols: pd.Series | None = None) -> dict:
    """vols: yearly volatility per symbol on the signal day; a missing one gets the average of the others."""
    if not chosen:
        return {}
    if how == "equal" or vols is None:
        return {s: 1 / len(chosen) for s in chosen}
    v = vols.reindex(chosen).astype(float)
    v = v.where(v > 0)
    v = v.fillna(v.mean() if v.notna().any() else 1.0)
    inverse = 1 / v
    return (inverse / inverse.sum()).to_dict()


# Portfolio weights for one rebalance: chosen stocks share len(chosen) / N; empty places go to the safe asset.
def target_weights(table: pd.DataFrame, held: list[str], settings: StockMomentumSettings,
                   vols: pd.Series | None) -> tuple[dict, list[str]]:
    """Returns (weights, chosen). Index filter says no -> everything in LIQUIDBEES (or cash)."""
    safe = settings.filters.safe_asset
    if not table.attrs.get("market_ok", True):
        return ({LIQUID: 1.0} if safe == LIQUID else {}), []
    chosen = choose(table, held, settings.top_n, settings.hold_rank)
    share = len(chosen) / settings.top_n
    weights = {s: w * share for s, w in stock_weights(chosen, settings.weighting, vols).items()}
    if share < 1 and safe == LIQUID:
        weights[LIQUID] = 1 - share
    return weights, chosen


# Yearly volatility of daily moves over the last `days` rows, for every day at once (row = that day's close).
def rolling_volatility(close: pd.DataFrame, days: int) -> pd.DataFrame:
    return close.pct_change(fill_method=None).rolling(days, min_periods=days).std() * math.sqrt(252)


# ---------------------------------------------------------------- the backtest

# The money you would have each day: cash + units x price, worked out for all days at once.
def equity_curve(positions: list[tuple], marks: pd.DataFrame, window: pd.DatetimeIndex) -> pd.Series:
    """positions: (day, cash, {symbol: units}) after each rebalance; units stay the same until the next one."""
    days = [d for d, _, _ in positions]
    units = pd.DataFrame([u for _, _, u in positions], index=pd.DatetimeIndex(days)).fillna(0.0)
    cash = pd.Series([c for _, c, _ in positions], index=units.index)
    every = units.index.union(window)                     # the starting cash row is before the first day
    units = units.reindex(every).ffill().fillna(0.0).reindex(window)
    cash = cash.reindex(every).ffill().reindex(window)
    value = (units * marks.reindex(index=window, columns=units.columns)).sum(axis=1) + cash
    return value.round(2)


# Run the backtest. data: StockData (default: read from PostgreSQL); progress(percent, message) for a progress bar.
def run_momentum_backtest(settings: StockMomentumSettings, progress=None, data: StockData | None = None,
                          allow_missing: bool = False, url: str | None = None, name: str | None = None) -> BacktestResult:
    settings.check()
    if progress:
        progress(1, "Reading prices from the database")
    data = data or load_stock_data(settings, url, allow_missing)
    close = data.close.sort_index()
    last_day = close.index[-1].date()
    end = min(settings.end or last_day, last_day)
    window = close.index[(close.index >= pd.Timestamp(settings.start)) & (close.index <= pd.Timestamp(end))]
    if len(window) < 2:
        raise ValueError(f"no stored prices between {settings.start} and {end}")
    pairs = rebalance_days(close.index, settings.start, end, settings.rebalance)
    ranker = MomentumRanker(close, data.volume, data.index_close, settings.filters)
    vols = rolling_volatility(close, settings.vol_days) if settings.weighting == "inverse_vol" else None

    prices = close.copy()                                  # fill prices: NaN = no trade that day
    years = (prices.index - prices.index[0]).days / 365.0
    prices[LIQUID] = LIQUID_START_PRICE * (1 + settings.liquid_yield_pct / 100) ** years
    marks = prices.ffill()                                 # value of what is held (last known price)

    book = Portfolio(settings.capital, settings.costs, settings.tax,
                     lambda sym: ("etf_other", "debt") if sym == LIQUID else ("equity_delivery", "equity"))
    positions = [(window[0] - pd.Timedelta(days=1), settings.capital, {})]
    log_rows, holding_rows, first_invested = [], [], None
    for n, (signal, trade) in enumerate(pairs, start=1):
        if trade not in window:
            continue
        table = ranker.rank(signal)
        held = [s for s in book.lots if s != LIQUID]
        weights, chosen = target_weights(table, held, settings,
                                         vols.loc[signal] if vols is not None else None)
        row = rebalance_to(book, weights, prices.loc[trade], marks.loc[trade], trade.date(), signal.date(),
                           settings.drift_band_pct, buy_last=(LIQUID,))
        row["market_ok"] = bool(table.attrs.get("market_ok", True))
        row["stocks"] = len([s for s in book.lots if s != LIQUID])
        log_rows.append(row)
        holding_rows += _holdings_rows(book, marks.loc[trade], trade.date(), signal.date(), table)
        positions.append((trade, book.cash, {s: book.units(s) for s in book.lots}))
        if first_invested is None and row["stocks"]:
            first_invested = trade.date()
        if progress:
            progress(5 + 90 * n / len(pairs), f"Rebalance {n} of {len(pairs)}: {trade:%d-%b-%Y}")

    equity = equity_curve(positions, marks, window).rename("equity")
    equity.index = [d.date() for d in equity.index]
    equity.index.name = "date"
    start, last = window[0].date(), window[-1].date()
    daily = equity.diff().fillna(equity.iloc[0] - settings.capital).round(2).rename("pnl")
    metrics = compute_metrics([t.pnl for t in book.trades], daily, settings.capital, start, last)
    metrics.update(curve_metrics(equity, settings.capital, start, last))
    metrics["total_pnl"] = to_paise(equity.iloc[-1] - settings.capital)
    metrics["return_pct"] = metrics["total_return_pct"]
    benchmarks = _benchmarks(data, equity, settings.capital, start, last)
    nifty = benchmarks.get(NIFTY_NAME)
    weighting = "inverse-volatility" if settings.weighting == "inverse_vol" else "equal"
    result = BacktestResult(
        name=name or f"Momentum: top {settings.top_n} of {settings.universe}, {settings.rebalance}, {weighting} weights",
        trades=book.trades, capital=settings.capital, start=start, end=last, daily_pnl=daily, equity=equity,
        metrics=metrics, benchmark=nifty, benchmark_name=NIFTY_NAME if nifty is not None else "",
        params=settings.to_params(), is_demo=data.is_demo)
    rebalances = pd.DataFrame(log_rows)
    turnover = sum(t.qty * (t.entry_price + t.exit_price) for t in book.trades)
    result.extras.update(
        benchmarks=benchmarks, rebalances=rebalances, holdings_log=pd.DataFrame(holding_rows),
        holdings=_holdings_rows(book, marks.loc[window[-1]], last, None, None), tax_by_year=book.tax.table(),
        charges=to_paise(book.charges), slippage=to_paise(book.slippage), tax_paid=to_paise(book.tax_paid),
        orders=book.orders, turnover=to_paise(turnover), final_value=float(equity.iloc[-1]),
        liquidation=liquidation_value(book, marks.loc[window[-1]], last),
        days_in_cash=int((rebalances["market_ok"] == False).sum()) if len(rebalances) else 0,   # noqa: E712
        first_invested=first_invested, notes=_notes(settings, first_invested))
    if progress:
        progress(99, "Working out the results")
    return result


# Notes under the results: survivorship bias, a late first investment, and the learning note.
def _notes(settings: StockMomentumSettings, first_invested: dt.date | None) -> list[str]:
    notes = [SURVIVORSHIP_NOTE]
    if first_invested is None:
        notes.append("No stock was ever bought: none passed the filters (or Nifty 50 stayed below its average).")
    elif first_invested > settings.start + dt.timedelta(days=45):
        notes.append(f"The first stocks were bought on {first_invested:%d-%b-%Y}; before that no stock passed the "
                     "filters or had enough price history, so the money waited in the safe asset.")
    return notes + [NOTE]


# The holdings right after a rebalance: one row per symbol (cash last).
def _holdings_rows(book: Portfolio, marks: pd.Series, day: dt.date, signal_day: dt.date | None,
                   table: pd.DataFrame | None) -> list[dict]:
    value = book.value(marks)
    ranks = table.set_index("symbol") if table is not None else None
    rows = []
    for sym, lots in book.lots.items():
        units = book.units(sym)
        rows.append({"rebalance_date": day, "signal_date": signal_day, "symbol": sym, "qty": units,
                     "avg_price": round(sum(lot.units * lot.price for lot in lots) / units, 2),
                     "price": round(float(marks[sym]), 2), "value": round(units * float(marks[sym]), 2),
                     "weight_pct": round(units * float(marks[sym]) / value * 100, 2),
                     "rank": (None if ranks is None or sym not in ranks.index or pd.isna(ranks.at[sym, "rank"])
                              else int(ranks.at[sym, "rank"])),
                     "score_pct": (None if ranks is None or sym not in ranks.index or pd.isna(ranks.at[sym, "score"])
                                   else round(float(ranks.at[sym, "score"]) * 100, 2))})
    rows.append({"rebalance_date": day, "signal_date": signal_day, "symbol": "Cash", "qty": None, "avg_price": None,
                 "price": None, "value": round(book.cash, 2), "weight_pct": round(book.cash / value * 100, 2),
                 "rank": None, "score_pct": None})
    return rows


# Nifty 50 rebased to the capital on day one; a Momentum 30 ETF (listed later) from its first day,
# starting at the strategy's value that day so the two lines are easy to compare from then on.
def _benchmarks(data: StockData, equity: pd.Series, capital: float, start: dt.date, end: dt.date) -> dict:
    out = {}
    nifty = rebase(data.index_close, capital, start, end)
    if nifty is not None:
        out[NIFTY_NAME] = nifty
    for name, closes in data.trackers.items():
        series = rebase(closes, capital, start, end)
        if series is None:
            continue
        first = series.index[0]
        if first in equity.index:
            series = series / capital * float(equity.loc[first])
        label = name if first <= start + dt.timedelta(days=7) else f"{name}, from {first:%d-%b-%Y}"
        out[label] = series.round(2)
    return out


# ---------------------------------------------------------------- measuring results

# Strategy vs every benchmark, side by side (each over the days it has).
def comparison_table(result: BacktestResult) -> pd.DataFrame:
    curves = {"Strategy": result.equity, **result.extras.get("benchmarks", {})}
    labels = {"final_value": "Final value (Rs)", "total_return_pct": "Total return %", "cagr_pct": "CAGR %",
              "max_drawdown_pct": "Max drawdown %", "volatility_pct": "Volatility % (yearly)",
              "sharpe": "Sharpe", "sortino": "Sortino", "calmar": "Calmar"}
    columns = {}
    for name, curve in curves.items():
        curve = curve.dropna()
        first_value = float(curve.iloc[0]) if name != "Strategy" else result.capital
        m = curve_metrics(curve, first_value, curve.index[0] if name != "Strategy" else result.start, result.end)
        columns[name] = {labels[k]: m.get(k) for k in labels}
    table = pd.DataFrame(columns)
    return table.astype(float).round(2).rename_axis("Measure").reset_index()


# Calendar-year returns of the strategy and Nifty 50.
def yearly_table(result: BacktestResult) -> pd.DataFrame:
    table = pd.DataFrame({"Strategy %": yearly_returns(result.equity, result.capital)})
    if result.benchmark is not None:
        table["Nifty 50 %"] = yearly_returns(result.benchmark, result.capital)
        table["Difference (points)"] = (table["Strategy %"] - table["Nifty 50 %"]).round(2)
    return table.rename_axis("Year").reset_index()


# ---------------------------------------------------------------- current ranks

# Today's ranking (from the last stored close) with names and sectors; the top N is marked.
def current_ranks(settings: StockMomentumSettings, date: dt.date | None = None, url: str | None = None) -> pd.DataFrame:
    """attrs: as_of, market_ok, index_close, index_average. Historical model output, not advice."""
    from marketlens.momentum.ranking import load_ranker
    ranker = load_ranker(settings.symbols(), date or dt.date.today(), settings.filters, url=url)
    table = ranker.rank(date or dt.date.today())
    info = universe_table(settings.universe).set_index("symbol")
    table["name"] = table["symbol"].map(info["name"])
    table["sector"] = table["symbol"].map(info["sector"]) if "sector" in info else ""
    table["score_pct"] = (table["score"] * 100).round(2)
    table["in_top_n"] = table["rank"].fillna(10_000).astype(int) <= settings.top_n
    attrs = dict(table.attrs)
    table = table[["rank", "symbol", "name", "sector", "score_pct", "passed", "in_top_n", "reason"]]
    table.attrs = attrs
    return table


# ---------------------------------------------------------------- saving

# Save a run (backtest_runs + backtest_trades) and the holdings after every rebalance (portfolio_holdings).
def save_momentum_run(result: BacktestResult, strategy_id: int | None = None, url: str | None = None) -> int:
    from marketlens.backtest.store import save_run
    from marketlens.db import get_session
    from marketlens.ingest.prices import ensure_instruments
    from marketlens.models import PortfolioHolding
    run_id = save_run(strategy_id, {"source": "momentum_engine"}, result, url=url)
    log_table = result.extras.get("holdings_log", pd.DataFrame())
    stocks = log_table[log_table["symbol"] != "Cash"] if len(log_table) else log_table
    if len(stocks):
        with get_session(url) as session:
            ids, _ = ensure_instruments(session, sorted(set(stocks["symbol"]) - {LIQUID}), "EQUITY")
            if LIQUID in set(stocks["symbol"]):
                ids.update(ensure_instruments(session, [LIQUID], "ETF")[0])
            session.add_all([PortfolioHolding(
                strategy_id=strategy_id, run_id=run_id, rebalance_date=r.rebalance_date,
                instrument_id=ids[r.symbol], qty=r.qty, avg_price=r.avg_price, price=r.price, value=r.value,
                weight=r.weight_pct) for r in stocks.itertuples()])
            session.commit()
    log.info("Momentum run %s saved with %d holding rows", run_id, len(stocks))
    return run_id


# Buys and sells on each rebalance of a saved run, worked out from its holdings and trades.
def rebalance_history(run_id: int, url: str | None = None) -> list[dict]:
    """[{date, buys: [{symbol, qty, price}], sells: [{symbol, qty, price}], holdings, value}], oldest first."""
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import BacktestRun, BacktestTrade, Instrument, PortfolioHolding
    with get_session(url) as session:
        if session.get(BacktestRun, run_id) is None:
            raise LookupError(f"No backtest run with id {run_id}")
        rows = session.execute(
            select(PortfolioHolding.rebalance_date, Instrument.symbol, PortfolioHolding.qty, PortfolioHolding.price,
                   PortfolioHolding.value)
            .join(Instrument, Instrument.id == PortfolioHolding.instrument_id)
            .where(PortfolioHolding.run_id == run_id).order_by(PortfolioHolding.rebalance_date)).all()
        trades = session.execute(
            select(BacktestTrade.symbol, BacktestTrade.qty, BacktestTrade.exit_time, BacktestTrade.exit_price)
            .where(BacktestTrade.run_id == run_id)).all()
    from marketlens.backtest.engine import ist_date
    sold = {}
    for sym, qty, when, price in trades:                  # every sell, by (day, symbol): units and money
        key = (ist_date(when), sym)
        units, money = sold.get(key, (0, 0.0))
        sold[key] = (units + qty, money + qty * float(price))
    by_day = {}
    for day, sym, qty, price, value in rows:
        by_day.setdefault(day, {})[sym] = (float(qty), float(price or 0), float(value or 0))
    history, before = [], {}
    for day in sorted(by_day):
        now = by_day[day]
        buys = [{"symbol": s, "qty": q - before.get(s, (0,))[0], "price": p}
                for s, (q, p, _) in now.items() if q > before.get(s, (0,))[0]]
        sells = []
        for s, (q, _, _) in before.items():
            left = now.get(s, (0,))[0]
            if left < q:
                units, money = sold.get((day, s), (q - left, 0.0))
                sells.append({"symbol": s, "qty": q - left, "price": round(money / units, 2) if money else None})
        history.append({"date": day, "buys": sorted(buys, key=lambda b: b["symbol"]),
                        "sells": sorted(sells, key=lambda b: b["symbol"]), "holdings": len(now),
                        "value": round(sum(v for _, _, v in now.values()), 2)})
        before = now
    return history


# Save (or update) the settings as a "momentum" strategy in the Strategy Library; returns its id.
def save_momentum_strategy(name: str, settings: StockMomentumSettings, notes: str = "", url: str | None = None) -> int:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Strategy
    if not name.strip():
        raise ValueError("Give the strategy a name")
    settings.check()
    params = {**settings.to_params(), "source": "momentum_engine"}
    with get_session(url) as session:
        strategy = session.scalar(select(Strategy).where(Strategy.name == name.strip()))
        if strategy is None:
            strategy = Strategy(name=name.strip(), kind="momentum", params=params, description=notes or None)
            session.add(strategy)
        elif strategy.kind != "momentum":
            raise ValueError(f"'{name}' is already a {strategy.kind} strategy: choose another name")
        else:
            strategy.params, strategy.description = params, notes or strategy.description
        session.commit()
        return strategy.id


# Settings of a saved momentum strategy.
def load_momentum_strategy(strategy_id: int, url: str | None = None) -> StockMomentumSettings:
    from marketlens.db import get_session
    from marketlens.models import Strategy
    with get_session(url) as session:
        strategy = session.get(Strategy, strategy_id)
        if strategy is None or strategy.kind != "momentum":
            raise LookupError(f"No momentum strategy with id {strategy_id}")
        return StockMomentumSettings.from_params(strategy.params)
