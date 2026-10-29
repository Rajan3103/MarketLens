"""The shapes every backtest result uses: Trade and BacktestResult.

Every backtester (options, momentum, ETF, signals) fills a list of Trade objects and
calls BacktestResult.build(...). That works out daily P&L, the equity curve and all the
metrics, so every page, chart, Excel report and database row reads the same numbers.

Costs ALWAYS come from marketlens/costs.py: a Trade works out its own brokerage, taxes
and slippage when it is created, so no backtester can forget them.
"""
import datetime as dt
from dataclasses import InitVar, dataclass, field

import numpy as np
import pandas as pd

from marketlens.backtest.metrics import compute_metrics, drawdown, equity_curve
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings, to_paise, trade_costs


@dataclass
class Trade:
    """One closed trade (one leg of a strategy). P&L is in rupees for the whole qty.

    side BUY  = bought first, sold later (profits when the price rises)
    side SELL = sold first, bought back later (profits when the price falls)
    exit_price 0 = an option that expired worthless (no closing order, so no closing costs).
    """
    symbol: str
    side: str
    qty: int
    entry_time: dt.datetime
    entry_price: float
    exit_time: dt.datetime
    exit_price: float
    segment: str = "options"            # options, futures, equity_intraday or equity_delivery
    leg_no: int | None = None
    exit_reason: str | None = None      # SL, TARGET, TRAIL_SL, COST_SL, M2M_SL, M2M_TARGET, M2M_TRAIL, TIME ...
    note: str | None = None             # extra detail, e.g. "SL and target both touched in this minute"
    costs: float | None = None          # leave empty: worked out from costs.py
    settings: InitVar[CostSettings | None] = None

    # Check the trade and work out its costs with costs.py.
    def __post_init__(self, settings):
        self.side = self.side.upper()
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, not {self.side!r}")
        if self.qty <= 0:
            raise ValueError("qty must be above 0")
        if self.entry_price <= 0 or self.exit_price < 0:
            raise ValueError("entry price must be above 0 and exit price 0 or more")
        if self.exit_time < self.entry_time:
            raise ValueError("exit time is before entry time")
        if self.costs is None:
            self.costs = order_costs(self.segment, self.side, self.entry_price, self.exit_price,
                                     self.qty, settings)

    # +1 for a bought (long) trade, -1 for a sold (short) trade.
    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1

    # P&L before costs.
    @property
    def gross_pnl(self) -> float:
        return to_paise(self.sign * (self.exit_price - self.entry_price) * self.qty)

    # P&L after costs: what really reaches your account.
    @property
    def pnl(self) -> float:
        return to_paise(self.gross_pnl - self.costs)

    # One row for the trade list, CSV, Excel and the database.
    def to_row(self) -> dict:
        return {"leg_no": self.leg_no, "symbol": self.symbol, "side": self.side, "qty": self.qty,
                "entry_time": self.entry_time, "entry_price": self.entry_price,
                "exit_time": self.exit_time, "exit_price": self.exit_price,
                "exit_reason": self.exit_reason, "note": self.note, "gross_pnl": self.gross_pnl,
                "costs": self.costs, "pnl": self.pnl}


TRADE_COLUMNS = ["leg_no", "symbol", "side", "qty", "entry_time", "entry_price", "exit_time",
                 "exit_price", "exit_reason", "note", "gross_pnl", "costs", "pnl"]


# Costs of opening and closing one trade, from costs.py (no closing order when the exit price is 0).
def order_costs(segment: str, side: str, entry_price: float, exit_price: float, qty: int,
                settings: CostSettings | None = None) -> float:
    closing_side = "SELL" if side == "BUY" else "BUY"
    total = trade_costs(segment, side, entry_price, qty, settings).total
    if exit_price > 0:
        total += trade_costs(segment, closing_side, exit_price, qty, settings).total
    return to_paise(total)


# The calendar date (India time) of a trade time.
def ist_date(when) -> dt.date:
    stamp = pd.Timestamp(when)
    return (stamp.tz_convert(IST) if stamp.tzinfo else stamp).date()


@dataclass
class BacktestResult:
    """Everything one backtest produced. Make it with BacktestResult.build(...)."""
    name: str
    trades: list[Trade]
    capital: float
    start: dt.date
    end: dt.date
    daily_pnl: pd.Series                     # net P&L per day (index: date), days with trades only
    equity: pd.Series                        # account value at the end of each of those days
    metrics: dict
    benchmark: pd.Series | None = None       # the benchmark rebased to the same capital (index: date)
    benchmark_name: str = ""
    params: dict = field(default_factory=dict)
    is_demo: bool = False
    extras: dict = field(default_factory=dict)   # anything a backtester adds (skipped days, MTM, run id)

    # Build a result from closed trades: daily P&L, equity, metrics and the rebased benchmark.
    @classmethod
    def build(cls, trades: list[Trade], capital: float, start: dt.date, end: dt.date,
              name: str = "Backtest", benchmark: pd.Series | None = None, benchmark_name: str = "",
              params: dict | None = None, is_demo: bool = False) -> "BacktestResult":
        """benchmark: daily closing prices (e.g. NIFTY) indexed by date; they are scaled so the
        benchmark starts with the same capital, which makes the two lines easy to compare."""
        if capital <= 0:
            raise ValueError("capital must be above 0")
        if end < start:
            raise ValueError("end date is before start date")
        trades = sorted(trades, key=lambda t: (t.exit_time, t.leg_no or 0))
        daily = daily_pnl_of(trades)
        pnls = [t.pnl for t in trades]
        metrics = compute_metrics(pnls, daily, capital, start, end)
        return cls(name=name, trades=trades, capital=capital, start=start, end=end, daily_pnl=daily,
                   equity=equity_curve(daily, capital), metrics=metrics,
                   benchmark=rebase(benchmark, capital, start, end), benchmark_name=benchmark_name,
                   params=params or {}, is_demo=is_demo)

    # All trades as a table (one row per trade, columns TRADE_COLUMNS).
    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([t.to_row() for t in self.trades], columns=TRADE_COLUMNS)

    # Daily P&L, equity and drawdown in one table (the "Daily P&L" sheet).
    def daily_frame(self) -> pd.DataFrame:
        dd = drawdown(self.equity, self.capital)
        table = pd.DataFrame({"date": self.daily_pnl.index, "pnl": self.daily_pnl.values,
                              "equity": dd["equity"].values, "drawdown_rs": dd["dd_rs"].values,
                              "drawdown_pct": dd["dd_pct"].values})
        trades = pd.Series([ist_date(t.exit_time) for t in self.trades]).value_counts()
        table["trades"] = table["date"].map(trades).fillna(0).astype(int)
        return table


# Add up net trade P&L per day (by the day the trade closed).
def daily_pnl_of(trades: list[Trade]) -> pd.Series:
    if not trades:
        return pd.Series(dtype=float, name="pnl")
    days = pd.Series([t.pnl for t in trades], index=[ist_date(t.exit_time) for t in trades])
    daily = days.groupby(level=0).sum().sort_index()
    daily.index.name, daily.name = "date", "pnl"
    return daily.round(2)


# Scale benchmark prices so they start at `capital` (only the backtest period is kept).
def rebase(prices: pd.Series | None, capital: float, start: dt.date, end: dt.date):
    if prices is None or len(prices) == 0:
        return None
    prices = prices.copy()
    prices.index = [ist_date(i) for i in prices.index]
    prices = prices.sort_index()
    prices = prices[(prices.index >= start) & (prices.index <= end)].dropna()
    if prices.empty:
        return None
    rebased = capital * prices / prices.iloc[0]
    rebased.index.name, rebased.name = "date", "benchmark"
    return rebased


# Made-up trades to preview the report before real option data is downloaded (labelled DEMO).
def sample_result(capital: float = 500_000, year: int = 2025, seed: int = 7) -> BacktestResult:
    """A short straddle every weekday: sold at 09:20, bought back at 15:15. Random but repeatable."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(f"{year}-01-01", f"{year}-12-31")
    trades, level = [], 23_500.0
    closes = {}
    for day in days:
        level *= 1 + rng.normal(0.0004, 0.009)
        closes[day.date()] = round(level, 2)
        entry = dt.datetime.combine(day.date(), dt.time(9, 20), IST)
        exit_ = dt.datetime.combine(day.date(), dt.time(15, 15), IST)
        for leg, kind in enumerate(("CE", "PE"), start=1):
            sold = round(rng.uniform(90, 140), 1)
            bought = max(round(sold * (1 + rng.normal(-0.02, 0.35)), 1), 0.05)   # about break-even
            trades.append(Trade(f"NIFTY ATM {kind}", "SELL", 65, entry, sold, exit_, bought,
                                leg_no=leg, exit_reason="TIME"))
    return BacktestResult.build(trades, capital, days[0].date(), days[-1].date(),
                                name="DEMO sample: NIFTY short straddle 09:20-15:15",
                                benchmark=pd.Series(closes), benchmark_name="NIFTY (DEMO)",
                                params={"underlying": "NIFTY", "lots": 1}, is_demo=True)
