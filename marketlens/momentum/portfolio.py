"""The money side of the momentum backtests (modules 8 and 9): cash, lots, costs, tax and results.

    book = Portfolio(capital, costs, TaxSettings(), classify=lambda sym: ("equity_delivery", "equity"))
    book.buy("TCS", 100_000, 3500.0, day); book.sell("TCS", 10, 3600.0, day, "rotated out"); book.pay_tax(day)
    rebalance_days(calendar, start, end, "monthly")   # (signal day, trade day) pairs, no look-ahead
    curve_metrics(equity, capital, start, end)         # CAGR, drawdown, Sharpe, Sortino, Calmar

Shared by etf_bt.py (ETFs) and portfolio_bt.py (Nifty 50 / 200 stocks). Capital gains tax follows today's
Indian rules (rates are settings): equity short-term (12 months or less) 20 %, long-term 12.5 % above
Rs 1.25 lakh a year; gold / silver / international short-term at the slab rate, long-term 12.5 %; liquid /
debt at the slab rate; plus 4 % cess. Losses are set off the Indian way and carried forward.
"""
import copy
import datetime as dt
import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from marketlens.backtest.engine import Trade
from marketlens.backtest.metrics import cagr, calmar, daily_returns, max_drawdown, sharpe, sortino
from marketlens.broker.candles import IST
from marketlens.costs import CostSettings, apply_slippage, to_paise, trade_costs

TRADE_TIME = dt.time(15, 30)
NO_COSTS = CostSettings(brokerage_per_order=0, brokerage_cap_pct=None, delivery_brokerage=0,
                        slippage_value=0, include_taxes=False, dp_charge=0)


@dataclass
class TaxSettings:
    """Capital gains tax rates in percent (today's rules, change them if they change)."""
    enabled: bool = True
    equity_stcg_pct: float = 20.0       # equity (stocks, equity ETFs) held 12 months or less
    equity_ltcg_pct: float = 12.5       # equity held more than 12 months ...
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
    """Units of one stock or ETF bought on one day (sold first-in, first-out)."""
    units: int
    price: float                 # fill price (after slippage)
    day: dt.date
    charges_per_unit: float      # all buy charges / units (for the trade list)
    cost_per_unit: float         # tax cost: price + buy charges except STT


class Portfolio:
    """Cash, lots of each stock or ETF, the trade list, costs and the tax book."""

    # Start with all capital in cash. classify(symbol) -> (costs.py segment, tax class: equity / other / debt).
    def __init__(self, capital: float, costs: CostSettings, tax: TaxSettings, classify):
        self.cash = capital
        self.lots: dict[str, list[Lot]] = {}
        self.costs = costs
        self.no_slip = CostSettings(**{**asdict(costs), "slippage_value": 0.0})
        self.tax = TaxBook(tax)
        self.classify = classify
        self.trades: list[Trade] = []
        self.charges = self.slippage = self.tax_paid = 0.0
        self.orders = 0

    # Units held of one symbol.
    def units(self, sym: str) -> int:
        return sum(lot.units for lot in self.lots.get(sym, []))

    # Value of all holdings at these prices (plus cash).
    def value(self, prices: pd.Series) -> float:
        return self.cash + sum(self.units(sym) * prices[sym] for sym in self.lots)

    # Sell units of one symbol at `price` (first-in, first-out); records trades and realised gains.
    def sell(self, sym: str, units: int, price: float, day: dt.date, reason: str):
        units = min(units, self.units(sym))
        if units <= 0:
            return
        segment, klass = self.classify(sym)
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
            self.tax.add(day, klass, gain, long_term)
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
        segment, _ = self.classify(sym)
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


# One rebalance: sell what left, trim what grew too big, pay tax, buy what is missing.
def rebalance_to(book: Portfolio, weights: dict, prices: pd.Series, marks: pd.Series, day: dt.date,
                 signal_day: dt.date, band_pct: float, buy_last: tuple = ()) -> dict:
    """weights: symbol -> share of the portfolio (the rest stays in cash). prices: today's fill prices
    (NaN = no trade today: that symbol is kept / not bought). A kept symbol is topped up or trimmed only
    when it is more than band_pct % away from its weight. buy_last: bought after the others (the safe asset)."""
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
    for sym, weight in sorted(weights.items(), key=lambda kv: kv[0] in buy_last):
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


# Calendar-year returns of a value curve (the first year is measured from the starting capital).
def yearly_returns(curve: pd.Series, capital: float) -> pd.Series:
    curve = curve.dropna()
    year_end = curve.groupby(pd.to_datetime(pd.Series(curve.index)).dt.year.values).last()
    before = pd.Series([capital, *year_end.values[:-1]], index=year_end.index)
    return ((year_end / before - 1) * 100).round(2)
