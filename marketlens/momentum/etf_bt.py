"""ETF momentum rotation backtest (module 9): hold the top K ETFs, rebalance monthly or weekly.

    data = load_etf_data()                                   # stored daily prices, repaired
    result = run_etf_backtest(data, EtfBacktestSettings(symbols=[...], top_k=2))

How one rebalance works (no look-ahead):
1. Signal: at the close of the last trading day of the month (or week), rank the ETFs with
   ranking.py using only prices up to that close.
2. Trade: at the close of the NEXT trading day. (Yahoo's ETF open prices are often stray first
   trades far from the real price, so the close is the honest price to fill at.)
3. Sell what dropped out (and trim winners that grew past their weight), pay the capital gains
   tax for the financial year so far, then buy the new ETFs with whole units.
Empty slots (fewer than K ETFs passed) go to LIQUIDBEES or stay in cash.

Costs come from costs.py (segments etf_equity / etf_other: brokerage, STT, exchange, SEBI, stamp,
GST, DP charge); slippage is in the fill price. Capital gains tax follows today's Indian rules,
applied to the whole period (rates are settings):
- equity ETFs:          short-term (12 months or less) 20%, long-term 12.5% above Rs 1.25 lakh a year
- gold / silver / international ETFs: short-term at your slab rate, long-term 12.5%
- liquid ETFs (LIQUIDBEES): every gain at your slab rate
- plus 4% cess; losses set off the Indian way and carried forward to later years.
"""
import copy
import datetime as dt
import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from marketlens.backtest.engine import BacktestResult, Trade, rebase
from marketlens.backtest.metrics import (cagr, calmar, compute_metrics, daily_returns, max_drawdown,
                                         sharpe, sortino)
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings, apply_slippage, to_paise, trade_costs
from marketlens.ingest.clean import repair_scale_errors
from marketlens.logger import get_logger
from marketlens.momentum.ranking import METHODS, MomentumRanker, MomentumSettings, momentum_scores

log = get_logger(__name__)

LIQUID = "LIQUIDBEES"
BENCHMARK = "NIFTYBEES"                      # Nifty 50 with dividends (what you could really buy)
BENCHMARK_NAME = "Nifty 50 (NIFTYBEES)"
LIQUID_START_PRICE = 1000.0
REBALANCE = ("monthly", "weekly")
TRADE_TIME = dt.time(15, 30)
LOOKBACK_CHOICES = {21: "1 month", 63: "3 months", 126: "6 months", 252: "12 months"}
SWEEP_SETS = [[21], [63], [126], [252], [63, 126, 252], [21, 63, 126]]
NO_COSTS = CostSettings(brokerage_per_order=0, brokerage_cap_pct=None, delivery_brokerage=0,
                        slippage_value=0, include_taxes=False, dp_charge=0)


@dataclass
class TaxSettings:
    """Capital gains tax rates in percent (today's rules, change them if they change)."""
    enabled: bool = True
    equity_stcg_pct: float = 20.0       # equity ETFs held 12 months or less
    equity_ltcg_pct: float = 12.5       # equity ETFs held more than 12 months ...
    ltcg_exemption: float = 125_000.0   # ... after the first Rs 1.25 lakh of such gains each financial year
    other_ltcg_pct: float = 12.5        # gold / silver / international ETFs held more than 12 months
    slab_pct: float = 30.0              # your income tax slab: short-term gold / silver / international, all liquid
    cess_pct: float = 4.0               # health and education cess on the tax
    long_term_months: int = 12

    # Refuse negative rates.
    def check(self):
        if min(self.equity_stcg_pct, self.equity_ltcg_pct, self.ltcg_exemption, self.other_ltcg_pct,
               self.slab_pct, self.cess_pct) < 0 or self.long_term_months < 1:
            raise ValueError("tax rates and the exemption cannot be negative")


@dataclass
class EtfBacktestSettings:
    """Everything one ETF momentum backtest needs."""
    symbols: list
    top_k: int = 2
    lookbacks: list = field(default_factory=lambda: [63, 126, 252])
    weights: list = field(default_factory=lambda: [1, 1, 1])
    method: str = "return"
    skip_recent: int = 0
    ma_days: int = 0                      # also require price above its N-day average; 0 = off
    rebalance: str = "monthly"
    abs_filter: bool = True               # an ETF must itself be rising (absolute momentum) ...
    abs_threshold_pct: float = 0.0        # ... by more than this % over the lookbacks
    safe_asset: str = LIQUID              # empty slots: LIQUIDBEES or CASH
    liquid_source: str = "yield"          # LIQUIDBEES return: a fixed yearly yield, or the stored prices
    liquid_yield_pct: float = 5.0
    drift_band_pct: float = 10.0          # re-balance a kept ETF only when it is this % away from its weight
    start: dt.date = dt.date(2018, 1, 1)
    end: dt.date | None = None            # None = the last stored day
    capital: float = 1_000_000.0
    costs: CostSettings = field(default_factory=lambda: CostSettings(slippage_value=0.05))
    tax: TaxSettings = field(default_factory=TaxSettings)

    # Stop with a clear message when a setting cannot work.
    def check(self):
        if not 1 <= self.top_k <= 5:
            raise ValueError("top K must be 1 to 5")
        if self.rebalance not in REBALANCE:
            raise ValueError(f"rebalance must be one of {REBALANCE}")
        if self.safe_asset not in (LIQUID, "CASH"):
            raise ValueError("safe asset must be LIQUIDBEES or CASH")
        if self.liquid_source not in ("yield", "prices"):
            raise ValueError("liquid_source must be 'yield' or 'prices'")
        if self.capital < 10_000:
            raise ValueError("capital must be at least Rs 10,000")
        if self.drift_band_pct < 0:
            raise ValueError("the drift band cannot be negative")
        if self.end is not None and self.end <= self.start:
            raise ValueError("end date must be after the start date")
        if not [s for s in self.symbols if s != LIQUID]:
            raise ValueError("tick at least one ETF to rank (LIQUIDBEES is only the safe asset)")
        self.ranking().check()
        self.tax.check()

    # The ranking settings (ranking.py) these ETF settings stand for.
    def ranking(self) -> MomentumSettings:
        return MomentumSettings(lookbacks=list(self.lookbacks), weights=list(self.weights), method=self.method,
                                skip_recent=self.skip_recent, ma_days=self.ma_days, index_filter=False)

    # Plain values for the Strategy Library and the Excel report.
    def to_params(self) -> dict:
        values = asdict(self)
        values["start"] = self.start.isoformat()
        values["end"] = self.end.isoformat() if self.end else None
        return values

    # Settings back from to_params().
    @classmethod
    def from_params(cls, params: dict) -> "EtfBacktestSettings":
        values = {k: v for k, v in params.items() if k in cls.__dataclass_fields__}
        values["start"] = dt.date.fromisoformat(values["start"])
        values["end"] = dt.date.fromisoformat(values["end"]) if values.get("end") else None
        values["costs"] = CostSettings(**values.get("costs", {}))
        values["tax"] = TaxSettings(**values.get("tax", {}))
        return cls(**values)


# ---------------------------------------------------------------- price data

@dataclass
class EtfData:
    """Daily closes of every ETF (rows = dates), their categories, and where they came from."""
    closes: pd.DataFrame
    categories: dict
    is_demo: bool = False
    fixes: list = field(default_factory=list)      # repaired power-of-10 errors (symbol, date, factor)

    # The last day with a price.
    @property
    def last_day(self) -> dt.date:
        return self.closes.index[-1].date()

    # The first day each ETF has a price (ETFs listed later start later).
    def first_days(self) -> dict:
        return {s: self.closes[s].first_valid_index().date() for s in self.closes.columns
                if self.closes[s].first_valid_index() is not None}


# The ETF list (symbol, name, category) from data/universe/etfs.csv.
def etf_universe() -> pd.DataFrame:
    from marketlens.ingest.prices import load_universe
    return load_universe("etfs")


# Tax group of an ETF from its category: equity, other (gold, silver, international) or debt (liquid).
def tax_class(category: str) -> str:
    category = (category or "").lower()
    if category.startswith("equity"):
        return "equity"
    if category.startswith("cash") or "liquid" in category or "debt" in category:
        return "debt"
    return "other"


# The costs.py segment for an ETF: STT only on equity ETFs.
def segment_of(category: str) -> str:
    return "etf_equity" if tax_class(category) == "equity" else "etf_other"


# Closes of the stored ETFs (daily_prices), with Yahoo's power-of-10 errors repaired.
def load_etf_data(symbols: list[str] | None = None, url: str | None = None) -> EtfData:
    from marketlens.ingest.prices import load_prices
    universe = etf_universe()
    symbols = list(dict.fromkeys((symbols or universe["symbol"].tolist()) + [BENCHMARK, LIQUID]))
    closes = load_prices(symbols, dt.date(2000, 1, 1), dt.date.today(), url=url)
    if closes.empty:
        raise ValueError("No stored ETF prices. Download them first: python scripts/sync_prices.py --universe etfs")
    closes, _, fixes = repair_scale_errors(closes)
    if fixes:
        log.info("Repaired %d power-of-10 price errors: %s", len(fixes), fixes)
    categories = dict(zip(universe["symbol"], universe["category"]))
    return EtfData(closes, categories, False, fixes)


# Made-up ETF prices (labelled DEMO) so the page works before any prices are downloaded.
def demo_etf_data(symbols: list[str] | None = None, start: dt.date = dt.date(2015, 1, 1),
                  end: dt.date = dt.date(2026, 10, 30), seed: int = 11) -> EtfData:
    universe = etf_universe()
    symbols = list(dict.fromkeys((symbols or universe["symbol"].tolist()) + [BENCHMARK, LIQUID]))
    categories = dict(zip(universe["symbol"], universe["category"]))
    days = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    data = {}
    for sym in symbols:
        drift, vol = (0.0002, 0.0003) if sym == LIQUID else (rng.uniform(0.0, 0.0006), rng.uniform(0.008, 0.016))
        data[sym] = 100 * np.cumprod(1 + rng.normal(drift, vol, len(days)))
    return EtfData(pd.DataFrame(data, index=days).round(2), categories, True)


# ---------------------------------------------------------------- capital gains tax

GAIN_HEADS = ("st_equity", "st_slab", "lt_other", "lt_equity")


# Tax for one financial year, after setting off losses the Indian way.
def year_tax(gains: dict, carried_st: float, carried_lt: float, s: TaxSettings) -> tuple[float, float, float]:
    """gains: net gain per head (st_equity, st_slab, lt_other, lt_equity); a negative value is a loss.
    carried_st / carried_lt: losses brought forward from earlier years (positive numbers).

    Rules: a short-term loss can reduce short-term or long-term gains, a long-term loss only
    long-term gains; losses go first where they save the most tax. Equity long-term gains are
    tax-free up to the yearly exemption. Returns (tax, short-term loss left, long-term loss left).
    """
    st_rates = {"st_equity": s.equity_stcg_pct, "st_slab": s.slab_pct}
    positive = {head: max(gains.get(head, 0.0), 0.0) for head in GAIN_HEADS}
    st_loss = carried_st + sum(-min(gains.get(h, 0.0), 0.0) for h in st_rates)
    lt_loss = carried_lt + sum(-min(gains.get(h, 0.0), 0.0) for h in ("lt_other", "lt_equity"))

    # Use up `loss` against these heads in this order; returns what is left of the loss.
    def set_off(loss: float, heads: list[str]) -> float:
        for head in heads:
            used = min(loss, positive[head])
            positive[head] -= used
            loss -= used
        return loss

    lt_loss = set_off(lt_loss, ["lt_other", "lt_equity"])
    st_loss = set_off(st_loss, sorted(st_rates, key=st_rates.get, reverse=True) + ["lt_other", "lt_equity"])
    taxable_lt_equity = max(positive["lt_equity"] - s.ltcg_exemption, 0.0)
    tax = (positive["st_equity"] * s.equity_stcg_pct + positive["st_slab"] * s.slab_pct
           + positive["lt_other"] * s.other_ltcg_pct + taxable_lt_equity * s.equity_ltcg_pct) / 100
    return to_paise(tax * (1 + s.cess_pct / 100)), st_loss, lt_loss


# The Indian financial year a day falls in, named by its first year (e.g. 2025 = Apr-2025 to Mar-2026).
def financial_year(day: dt.date) -> int:
    return day.year if day.month >= 4 else day.year - 1


class TaxBook:
    """Realised gains of each financial year; the tax due so far is paid at every rebalance."""

    # Start with no gains and no losses carried forward.
    def __init__(self, settings: TaxSettings):
        self.s = settings
        self.fy = None
        self.gains = dict.fromkeys(GAIN_HEADS, 0.0)
        self.carried_st = self.carried_lt = 0.0
        self.paid = 0.0
        self.years = {}                    # fy -> {"gains": {...}, "tax": paid}

    # Close the old financial year when a day of a new one comes: its leftover losses carry forward.
    def _move_to(self, day: dt.date):
        fy = financial_year(day)
        if self.fy == fy:
            return
        if self.fy is not None:
            _, self.carried_st, self.carried_lt = year_tax(self.gains, self.carried_st, self.carried_lt, self.s)
        self.fy, self.gains, self.paid = fy, dict.fromkeys(GAIN_HEADS, 0.0), 0.0
        self.years[fy] = {"gains": self.gains, "tax": 0.0, "carried_in": self.carried_st + self.carried_lt}

    # Record one realised gain (negative = loss).
    def add(self, day: dt.date, klass: str, gain: float, long_term: bool):
        self._move_to(day)
        if klass == "equity":
            head = "lt_equity" if long_term else "st_equity"
        elif klass == "other":
            head = "lt_other" if long_term else "st_slab"
        else:
            head = "st_slab"                 # liquid / debt ETFs: always at the slab rate
        self.gains[head] += gain

    # Tax still to pay for this financial year (negative = refund of tax paid earlier in the year).
    def settle(self, day: dt.date) -> float:
        self._move_to(day)
        if not self.s.enabled:
            return 0.0
        due, _, _ = year_tax(self.gains, self.carried_st, self.carried_lt, self.s)
        payment = to_paise(due - self.paid)
        self.paid = due
        self.years[self.fy]["tax"] = due
        return payment

    # One row per financial year: gains by head and the tax paid.
    def table(self) -> pd.DataFrame:
        rows = [{"Financial year": f"FY {fy}-{str(fy + 1)[-2:]}",
                 "Short-term equity (Rs)": y["gains"]["st_equity"],
                 "Short-term at slab (Rs)": y["gains"]["st_slab"],
                 "Long-term equity (Rs)": y["gains"]["lt_equity"],
                 "Long-term other (Rs)": y["gains"]["lt_other"],
                 "Losses brought in (Rs)": y["carried_in"], "Tax paid (Rs)": y["tax"]}
                for fy, y in sorted(self.years.items())]
        return pd.DataFrame(rows).round(2)


# ---------------------------------------------------------------- the portfolio

@dataclass
class Lot:
    """Units of one ETF bought on one day (sold first-in, first-out)."""
    units: int
    price: float                 # fill price (after slippage)
    day: dt.date
    charges_per_unit: float      # all buy charges / units (for the trade list)
    cost_per_unit: float         # tax cost: price + buy charges except STT


class Portfolio:
    """Cash, ETF lots, the trade list, costs and the tax book."""

    # Start with all capital in cash.
    def __init__(self, capital: float, costs: CostSettings, tax: TaxSettings, categories: dict):
        self.cash = capital
        self.lots: dict[str, list[Lot]] = {}
        self.costs = costs
        self.no_slip = CostSettings(**{**asdict(costs), "slippage_value": 0.0})
        self.tax = TaxBook(tax)
        self.categories = categories
        self.trades: list[Trade] = []
        self.charges = self.slippage = self.tax_paid = 0.0
        self.orders = 0

    # Units held of one ETF.
    def units(self, sym: str) -> int:
        return sum(lot.units for lot in self.lots.get(sym, []))

    # Value of all holdings at these prices (plus cash).
    def value(self, prices: pd.Series) -> float:
        return self.cash + sum(self.units(sym) * prices[sym] for sym in self.lots)

    # Sell units of one ETF at `price` (first-in, first-out); records trades and realised gains.
    def sell(self, sym: str, units: int, price: float, day: dt.date, reason: str):
        units = min(units, self.units(sym))
        if units <= 0:
            return
        segment = segment_of(self.categories.get(sym, ""))
        fill = apply_slippage(price, "SELL", self.costs)
        cost = trade_costs(segment, "SELL", fill, units, self.no_slip)
        self.cash += fill * units - cost.charges
        self.charges += cost.charges
        self.slippage += (price - fill) * units
        self.orders += 1
        deductible = cost.charges - cost.stt              # STT is not allowed as a cost for tax
        when = dt.datetime.combine(day, TRADE_TIME, IST)
        left = units
        while left:
            lot = self.lots[sym][0]
            qty = min(left, lot.units)
            long_term = pd.Timestamp(day) > pd.Timestamp(lot.day) + pd.DateOffset(months=self.tax.s.long_term_months)
            gain = qty * fill - deductible * qty / units - lot.cost_per_unit * qty
            self.tax.add(day, tax_class(self.categories.get(sym, "")), gain, long_term)
            self.trades.append(Trade(
                sym, "BUY", qty, dt.datetime.combine(lot.day, TRADE_TIME, IST), lot.price, when, fill,
                segment=segment, exit_reason=reason, note="long-term" if long_term else "short-term",
                costs=to_paise(lot.charges_per_unit * qty + cost.charges * qty / units)))
            lot.units -= qty
            left -= qty
            if lot.units == 0:
                self.lots[sym].pop(0)
        if not self.lots[sym]:
            del self.lots[sym]

    # Buy as many whole units as `budget` (and the cash) allows, charges included.
    def buy(self, sym: str, budget: float, price: float, day: dt.date) -> int:
        segment = segment_of(self.categories.get(sym, ""))
        fill = apply_slippage(price, "BUY", self.costs)
        units = int(min(budget, self.cash) // fill)
        while units > 0:
            cost = trade_costs(segment, "BUY", fill, units, self.no_slip)
            if fill * units + cost.charges <= self.cash + 1e-9:
                break
            units -= 1
        if units <= 0:
            return 0
        self.cash -= fill * units + cost.charges
        self.charges += cost.charges
        self.slippage += (fill - price) * units
        self.orders += 1
        self.lots.setdefault(sym, []).append(Lot(units, fill, day, cost.charges / units,
                                                 fill + (cost.charges - cost.stt) / units))
        return units

    # Pay (or get back) the capital gains tax due for this financial year so far.
    def pay_tax(self, day: dt.date) -> float:
        payment = self.tax.settle(day)
        self.cash -= payment
        self.tax_paid += payment
        return payment


# ---------------------------------------------------------------- signals

# (signal day, trade day) pairs: the last trading day of each month / week, traded the next day.
def rebalance_days(calendar: pd.DatetimeIndex, start: dt.date, end: dt.date, how: str) -> list[tuple]:
    """The first signal is the last trading day before `start`, so money is invested on day one."""
    days = calendar[calendar <= pd.Timestamp(end)]
    if how == "monthly":
        period = days.year * 100 + days.month
    else:
        iso = days.isocalendar()
        period = (iso.year * 100 + iso.week).to_numpy()
    period = np.asarray(period)
    last_of_period = np.append(period[1:] != period[:-1], False)        # the next day is in a new period
    pairs = []
    before = days[days < pd.Timestamp(start)]
    if len(before):
        first_trade = days[days >= pd.Timestamp(start)]
        if len(first_trade):
            pairs.append((before[-1], first_trade[0]))
    for i in np.flatnonzero(last_of_period):
        if days[i + 1] >= pd.Timestamp(start) and (not pairs or days[i] > pairs[0][0]):
            pairs.append((days[i], days[i + 1]))
    if not pairs:
        window = days[days >= pd.Timestamp(start)]
        if len(window) > 1:
            pairs.append((window[0], window[1]))
    return pairs


# The ETFs that can be ranked (LIQUIDBEES is only the safe asset), with their closes.
def ranked_pool(data: EtfData, settings: EtfBacktestSettings) -> pd.DataFrame:
    pool = [s for s in settings.symbols if s != LIQUID and s in data.closes.columns]
    return data.closes[pool]


# The ranking table on one signal day, with the absolute momentum filter applied.
def signal_table(ranker: MomentumRanker, pool: pd.DataFrame, settings: EtfBacktestSettings, day) -> pd.DataFrame:
    """Columns: symbol, score, rank, passed, reason, abs_return_pct (rank 1 = best that passed)."""
    table = ranker.rank(day)
    absolute = momentum_scores(pool, day, settings.lookbacks, settings.weights, "return", settings.skip_recent) * 100
    table["abs_return_pct"] = table["symbol"].map(absolute)
    if settings.abs_filter:
        weak = table["passed"] & ~(table["abs_return_pct"] > settings.abs_threshold_pct)
        table.loc[weak, "reason"] = table.loc[weak, "abs_return_pct"].map(
            lambda r: f"absolute momentum {r:.1f}% not above {settings.abs_threshold_pct:g}%")
        table.loc[weak, "passed"] = False
        table = table.sort_values(["passed", "score"], ascending=[False, False], na_position="last")
        table["rank"] = pd.array([n + 1 if ok else pd.NA for n, ok in enumerate(table["passed"])], dtype="Int64")
    attrs = dict(table.attrs)
    table = table.reset_index(drop=True)
    table.attrs = attrs
    return table


# Target weights from a signal table: 1/K for each of the top K, the rest in LIQUIDBEES or cash.
def target_weights(table: pd.DataFrame, top_k: int, safe_asset: str) -> dict:
    chosen = table[table["passed"]].head(top_k)["symbol"].tolist()
    weights = {sym: 1 / top_k for sym in chosen}
    if len(chosen) < top_k and safe_asset == LIQUID:
        weights[LIQUID] = (top_k - len(chosen)) / top_k
    return weights


# Signal tables for every rebalance (they do not depend on K, so a sweep can reuse them).
def signal_tables(data: EtfData, settings: EtfBacktestSettings, pairs: list[tuple]) -> dict:
    pool = ranked_pool(data, settings)
    ranker = MomentumRanker(pool, settings=settings.ranking())
    return {signal: signal_table(ranker, pool, settings, signal) for signal, _ in pairs}


# Daily prices used for trading and valuation (LIQUIDBEES from a fixed yield, if chosen).
def trading_prices(data: EtfData, settings: EtfBacktestSettings) -> pd.DataFrame:
    closes = data.closes.copy()
    if settings.safe_asset == LIQUID and settings.liquid_source == "yield":
        years = (closes.index - closes.index[0]).days / 365.0
        closes[LIQUID] = LIQUID_START_PRICE * (1 + settings.liquid_yield_pct / 100) ** years
    return closes


# ---------------------------------------------------------------- the backtest

# Run the backtest. `signals` (from signal_tables) can be passed to skip re-ranking.
def run_etf_backtest(data: EtfData, settings: EtfBacktestSettings, signals: dict | None = None,
                     name: str | None = None) -> BacktestResult:
    settings.check()
    end = min(settings.end or data.last_day, data.last_day)
    raw = trading_prices(data, settings)
    calendar = raw.index
    window = calendar[(calendar >= pd.Timestamp(settings.start)) & (calendar <= pd.Timestamp(end))]
    if len(window) < 2:
        raise ValueError(f"no stored prices between {settings.start} and {end}")
    pairs = rebalance_days(calendar, settings.start, end, settings.rebalance)
    signals = signals if signals is not None else signal_tables(data, settings, pairs)
    trade_on = {trade: signal for signal, trade in pairs}
    marks = raw.ffill()

    book = Portfolio(settings.capital, settings.costs, settings.tax, data.categories)
    equity, log_rows, first_etf_day = {}, [], None
    for day in window:
        if day in trade_on:
            weights = target_weights(signals[trade_on[day]], settings.top_k, settings.safe_asset)
            if first_etf_day is None and any(sym != LIQUID for sym in weights):
                first_etf_day = day.date()
            log_rows.append(_rebalance(book, weights, raw.loc[day], marks.loc[day], day.date(),
                                       trade_on[day].date(), settings.drift_band_pct))
        equity[day.date()] = book.value(marks.loc[day])

    equity = pd.Series(equity, name="equity").round(2)
    equity.index.name = "date"
    start, last = window[0].date(), window[-1].date()
    daily = equity.diff().fillna(equity.iloc[0] - settings.capital).round(2).rename("pnl")
    liquidation = liquidation_value(book, marks.loc[window[-1]], last)
    metrics = compute_metrics([t.pnl for t in book.trades], daily, settings.capital, start, last)
    metrics.update(curve_metrics(equity, settings.capital, start, last))
    metrics["total_pnl"] = to_paise(equity.iloc[-1] - settings.capital)
    metrics["return_pct"] = metrics["total_return_pct"]
    result = BacktestResult(
        name=name or f"ETF momentum: top {settings.top_k}, {settings.rebalance}", trades=book.trades,
        capital=settings.capital, start=start, end=last, daily_pnl=daily, equity=equity, metrics=metrics,
        benchmark=rebase(data.closes[BENCHMARK], settings.capital, start, last) if BENCHMARK in data.closes else None,
        benchmark_name=BENCHMARK_NAME, params=settings.to_params(), is_demo=data.is_demo)
    result.extras.update(
        rebalances=pd.DataFrame(log_rows), tax_by_year=book.tax.table(), charges=to_paise(book.charges),
        slippage=to_paise(book.slippage), tax_paid=to_paise(book.tax_paid), orders=book.orders,
        final_value=float(equity.iloc[-1]), liquidation=liquidation, holdings=holdings_table(book, marks.loc[window[-1]]),
        fixes=data.fixes, first_days=data.first_days(), first_etf_day=first_etf_day)
    return result


# One rebalance: sell what left, trim what grew too big, pay tax, buy what is missing.
def _rebalance(book: Portfolio, weights: dict, prices: pd.Series, marks: pd.Series, day: dt.date,
               signal_day: dt.date, band_pct: float) -> dict:
    band = band_pct / 100
    value = book.value(marks)
    sold = []
    for sym in list(book.lots):
        if pd.isna(prices.get(sym, np.nan)):
            continue                                    # no trade in it today: keep it
        held = book.units(sym) * prices[sym]
        if sym not in weights:
            book.sell(sym, book.units(sym), prices[sym], day, "rotated out")
            sold.append(sym)
        elif held > weights[sym] * value * (1 + band):
            book.sell(sym, math.ceil((held - weights[sym] * value) / prices[sym]), prices[sym], day, "trimmed")
            sold.append(sym)
    tax = book.pay_tax(day) if sold else 0.0
    value = book.value(marks)
    bought = []
    for sym, weight in sorted(weights.items(), key=lambda kv: kv[0] == LIQUID):   # ETFs first, LIQUIDBEES last
        price = prices.get(sym, np.nan)
        if pd.isna(price):
            continue
        held = book.units(sym) * price
        if held < weight * value * (1 - band):
            if book.buy(sym, weight * value - held, price, day):
                bought.append(sym)
    return {"signal_day": signal_day, "trade_day": day,
            "target": ", ".join(f"{s} {w:.0%}" for s, w in weights.items()) or "cash",
            "sold": ", ".join(sold), "bought": ", ".join(bought), "tax_paid": tax,
            "value_after": round(book.value(marks), 2)}


# What you would really have if you sold everything on the last day: after exit costs and tax.
def liquidation_value(book: Portfolio, prices: pd.Series, day: dt.date) -> dict:
    final = copy.deepcopy(book)
    for sym in list(final.lots):
        final.sell(sym, final.units(sym), prices[sym], day, "end")
    tax = final.pay_tax(day)
    return {"value": round(final.cash, 2), "exit_costs": round(final.charges - book.charges, 2),
            "exit_slippage": round(final.slippage - book.slippage, 2), "exit_tax": tax}


# The holdings on a day: units, average buy price, value and weight.
def holdings_table(book: Portfolio, prices: pd.Series) -> pd.DataFrame:
    value = book.value(prices)
    rows = [{"ETF": sym, "Units": book.units(sym),
             "Avg buy price": round(sum(lot.units * lot.price for lot in lots) / book.units(sym), 2),
             "Last price": round(prices[sym], 2), "Value (Rs)": round(book.units(sym) * prices[sym], 2),
             "Weight %": round(book.units(sym) * prices[sym] / value * 100, 1)} for sym, lots in book.lots.items()]
    rows.append({"ETF": "Cash", "Units": None, "Avg buy price": None, "Last price": None,
                 "Value (Rs)": round(book.cash, 2), "Weight %": round(book.cash / value * 100, 1)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- measuring results

# Return, CAGR, drawdown, volatility, Sharpe, Sortino and Calmar from a daily value curve.
def curve_metrics(curve: pd.Series, capital: float, start: dt.date, end: dt.date) -> dict:
    curve = curve.dropna()
    if curve.empty:
        return {}
    daily = curve.diff().fillna(curve.iloc[0] - capital)
    returns = daily_returns(daily, capital)
    growth = cagr(capital, float(curve.iloc[-1]), (end - start).days)
    dd_rs, dd_pct = max_drawdown(curve, capital)
    return {"final_value": float(curve.iloc[-1]),
            "total_return_pct": (float(curve.iloc[-1]) / capital - 1) * 100, "cagr_pct": growth,
            "max_drawdown_rs": dd_rs, "max_drawdown_pct": dd_pct,
            "volatility_pct": float(returns.std(ddof=1) * math.sqrt(252) * 100) if len(returns) > 1 else None,
            "sharpe": sharpe(returns), "sortino": sortino(returns), "calmar": calmar(growth, dd_pct)}


# Strategy vs Nifty 50, side by side.
def comparison_table(result: BacktestResult) -> pd.DataFrame:
    rows = {"Strategy": curve_metrics(result.equity, result.capital, result.start, result.end)}
    if result.benchmark is not None:
        rows[result.benchmark_name] = curve_metrics(result.benchmark, result.capital, result.start, result.end)
    labels = {"final_value": "Final value (Rs)", "total_return_pct": "Total return %", "cagr_pct": "CAGR %",
              "max_drawdown_pct": "Max drawdown %", "volatility_pct": "Volatility % (yearly)",
              "sharpe": "Sharpe", "sortino": "Sortino", "calmar": "Calmar"}
    table = pd.DataFrame({name: {labels[k]: m.get(k) for k in labels} for name, m in rows.items()})
    return table.astype(float).round(2).rename_axis("Measure").reset_index()


# Calendar-year returns of a value curve (the first year is measured from the starting capital).
def yearly_returns(curve: pd.Series, capital: float) -> pd.Series:
    curve = curve.dropna()
    year_end = curve.groupby(pd.to_datetime(pd.Series(curve.index)).dt.year.values).last()
    before = pd.Series([capital, *year_end.values[:-1]], index=year_end.index)
    return ((year_end / before - 1) * 100).round(2)


# Yearly returns of the strategy and Nifty 50 in one table.
def yearly_table(result: BacktestResult) -> pd.DataFrame:
    table = pd.DataFrame({"Strategy %": yearly_returns(result.equity, result.capital)})
    if result.benchmark is not None:
        table[f"{result.benchmark_name} %"] = yearly_returns(result.benchmark, result.capital)
        table["Difference (points)"] = (table.iloc[:, 0] - table.iloc[:, 1]).round(2)
    return table.rename_axis("Year").reset_index()


# ---------------------------------------------------------------- the current signal

# What the strategy says to hold now (ranked at the last stored close).
def current_signal(data: EtfData, settings: EtfBacktestSettings) -> tuple[pd.DataFrame, dict]:
    pool = ranked_pool(data, settings)
    ranker = MomentumRanker(pool, settings=settings.ranking())
    table = signal_table(ranker, pool, settings, data.closes.index[-1])
    return table, target_weights(table, settings.top_k, settings.safe_asset)


# ---------------------------------------------------------------- check a claim

# A claimed total return turned into a yearly rate (CAGR %) over its period.
def claim_cagr(total_return_pct: float, start: dt.date, end: dt.date):
    return cagr(100.0, 100.0 * (1 + total_return_pct / 100), (end - start).days)


# Run the same strategy without costs, with costs, and with costs and tax, next to a claim.
def check_claim(data: EtfData, settings: EtfBacktestSettings, claimed_pct: float, start: dt.date,
                end: dt.date) -> dict:
    """Returns {"table": comparison, "results": {label: BacktestResult}, "steps": what costs and tax took}."""
    base = copy.deepcopy(settings)
    base.start, base.end = start, end
    runs = {
        "Before costs and tax": (NO_COSTS, False),
        "After costs": (base.costs, False),
        "After costs and tax": (base.costs, base.tax.enabled),
    }
    results, signals = {}, None
    for label, (costs, taxed) in runs.items():
        s = copy.deepcopy(base)
        s.costs, s.tax.enabled = costs, taxed
        if signals is None:
            signals = signal_tables(data, s, rebalance_days(trading_prices(data, s).index, s.start,
                                                            min(s.end, data.last_day), s.rebalance))
        results[label] = run_etf_backtest(data, s, signals, name=label)
    taxed = results["After costs and tax"]
    real_start, real_end = taxed.start, taxed.end
    rows = [{"Line": "Claim", "Total return %": claimed_pct, "CAGR %": claim_cagr(claimed_pct, start, end),
             "Final value of Rs 1 lakh": 100_000 * (1 + claimed_pct / 100)}]
    for label, result in results.items():
        m = result.metrics
        rows.append({"Line": f"My backtest: {label.lower()}", "Total return %": m["total_return_pct"],
                     "CAGR %": m["cagr_pct"], "Final value of Rs 1 lakh": 100_000 * (1 + m["total_return_pct"] / 100)})
    sold = taxed.extras["liquidation"]["value"] / taxed.capital * 100 - 100
    rows.append({"Line": "My backtest: after costs and tax, if everything is sold at the end",
                 "Total return %": sold, "CAGR %": cagr(100.0, 100 + sold, (real_end - real_start).days),
                 "Final value of Rs 1 lakh": 100_000 * (1 + sold / 100)})
    if taxed.benchmark is not None:
        bench = taxed.benchmark.iloc[-1] / taxed.capital * 100 - 100
        rows.append({"Line": BENCHMARK_NAME, "Total return %": bench,
                     "CAGR %": cagr(100.0, 100 + bench, (real_end - real_start).days),
                     "Final value of Rs 1 lakh": 100_000 * (1 + bench / 100)})
    table = pd.DataFrame(rows)
    table[["Total return %", "CAGR %", "Final value of Rs 1 lakh"]] = \
        table[["Total return %", "CAGR %", "Final value of Rs 1 lakh"]].astype(float).round(2)
    gross = results["Before costs and tax"].metrics["total_return_pct"]
    net = results["After costs"].metrics["total_return_pct"]
    after_tax = taxed.metrics["total_return_pct"]
    steps = {"costs_points": gross - net, "tax_points": net - after_tax, "exit_points": after_tax - sold,
             "charges_rs": taxed.extras["charges"], "slippage_rs": taxed.extras["slippage"],
             "tax_rs": taxed.extras["tax_paid"], "period": (real_start, real_end),
             "first_etf_day": taxed.extras["first_etf_day"]}
    return {"table": table, "results": results, "steps": steps}


# ---------------------------------------------------------------- parameter sweep

# Run every top K (1..5) with every lookback set; one row per run.
def parameter_sweep(data: EtfData, settings: EtfBacktestSettings, ks=range(1, 6), lookback_sets=None,
                    progress=None) -> pd.DataFrame:
    """progress(done, total) is called after each run (for a progress bar)."""
    lookback_sets = lookback_sets or SWEEP_SETS
    rows, total, done = [], len(lookback_sets) * len(list(ks)), 0
    end = min(settings.end or data.last_day, data.last_day)
    for lookbacks in lookback_sets:
        s = copy.deepcopy(settings)
        s.lookbacks, s.weights = list(lookbacks), [1] * len(lookbacks)
        pairs = rebalance_days(trading_prices(data, s).index, s.start, end, s.rebalance)
        signals = signal_tables(data, s, pairs)
        for k in ks:
            s.top_k = k
            m = run_etf_backtest(data, s, signals).metrics
            rows.append({"Lookbacks": " + ".join(LOOKBACK_CHOICES.get(n, f"{n} days") for n in lookbacks),
                         "Top K": k, "CAGR %": m["cagr_pct"], "Total return %": m["total_return_pct"],
                         "Max drawdown %": m["max_drawdown_pct"], "Sharpe": m["sharpe"],
                         "Final value (Rs)": m["final_value"]})
            done += 1
            if progress:
                progress(done, total)
    return pd.DataFrame(rows).round(2)


# ---------------------------------------------------------------- Strategy Library

# Save (or update) the settings as an "etf" strategy in the Strategy Library; returns its id.
def save_etf_strategy(name: str, settings: EtfBacktestSettings, notes: str = "", url: str | None = None) -> int:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Strategy
    if not name.strip():
        raise ValueError("Give the strategy a name")
    settings.check()
    params = {**settings.to_params(), "source": "etf_momentum"}
    with get_session(url) as session:
        strategy = session.scalar(select(Strategy).where(Strategy.name == name.strip()))
        if strategy is None:
            strategy = Strategy(name=name.strip(), kind="etf", params=params, description=notes or None)
            session.add(strategy)
        elif strategy.kind != "etf":
            raise ValueError(f"'{name}' is already a {strategy.kind} strategy: choose another name")
        else:
            strategy.params, strategy.description = params, notes or strategy.description
        session.commit()
        return strategy.id


# Settings of a saved ETF strategy.
def load_etf_strategy(strategy_id: int, url: str | None = None) -> EtfBacktestSettings:
    from marketlens.db import get_session
    from marketlens.models import Strategy
    with get_session(url) as session:
        strategy = session.get(Strategy, strategy_id)
        if strategy is None or strategy.kind != "etf":
            raise ValueError(f"no ETF strategy with id {strategy_id}")
        return EtfBacktestSettings.from_params(strategy.params)

