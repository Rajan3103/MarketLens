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
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from marketlens.backtest.engine import BacktestResult, rebase
from marketlens.backtest.metrics import cagr, compute_metrics
from marketlens.costs import CostSettings, to_paise
from marketlens.ingest.clean import repair_scale_errors
from marketlens.logger import get_logger
from marketlens.momentum.portfolio import (NO_COSTS, Portfolio, TaxBook, TaxSettings,  # noqa: F401  (tests use them)
                                           curve_metrics, liquidation_value, rebalance_days, rebalance_to, year_tax,
                                           yearly_returns)
from marketlens.momentum.ranking import METHODS, MomentumRanker, MomentumSettings, momentum_scores  # noqa: F401

log = get_logger(__name__)

LIQUID = "LIQUIDBEES"
BENCHMARK = "NIFTYBEES"                      # Nifty 50 with dividends (what you could really buy)
BENCHMARK_NAME = "Nifty 50 (NIFTYBEES)"
LIQUID_START_PRICE = 1000.0
REBALANCE = ("monthly", "weekly")
LOOKBACK_CHOICES = {21: "1 month", 63: "3 months", 126: "6 months", 252: "12 months"}
SWEEP_SETS = [[21], [63], [126], [252], [63, 126, 252], [21, 63, 126]]


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


# For the portfolio: symbol -> (costs.py segment, tax class), from the ETF categories.
def etf_classifier(categories: dict):
    return lambda sym: (segment_of(categories.get(sym, "")), tax_class(categories.get(sym, "")))


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


# ---------------------------------------------------------------- signals

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

    book = Portfolio(settings.capital, settings.costs, settings.tax, etf_classifier(data.categories))
    equity, log_rows, first_etf_day = {}, [], None
    for day in window:
        if day in trade_on:
            weights = target_weights(signals[trade_on[day]], settings.top_k, settings.safe_asset)
            if first_etf_day is None and any(sym != LIQUID for sym in weights):
                first_etf_day = day.date()
            log_rows.append(rebalance_to(book, weights, raw.loc[day], marks.loc[day], day.date(),
                                         trade_on[day].date(), settings.drift_band_pct, buy_last=(LIQUID,)))
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

