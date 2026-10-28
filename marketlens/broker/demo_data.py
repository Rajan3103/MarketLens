"""DEMO market data: realistic but FAKE, for use until the Dhan keys are added.

Same methods and answer shapes as DhanDataClient, so every page works without keys.
Every table it returns has source = "DEMO" (and OptionChain.source == "DEMO").

How the numbers are made:
  - spot price: the latest real close from yfinance (fixed sensible values when offline)
  - volatility: India VIX from yfinance (14% when offline)
  - option prices and Greeks: Black-Scholes (marketlens/options/pricing.py) with a small "smile"
  - past prices (candles, expired options): ONE repeatable price path per underlying,
    anchored at a fixed level on 1-Jan-2026, so every strike, the index minutes and
    futures agree with each other, and a download run twice gives the same numbers
  - minute bars inside a day run smoothly from the previous close to that day's close
"""
import datetime as dt
import math
import zlib
from functools import lru_cache

import numpy as np
import pandas as pd
import yfinance as yf

from marketlens.broker.candles import (COLUMNS, DAILY_MINUTES, INTRADAY_INTERVALS, IST, MARKET_CLOSE,
                                       OPTION_COLUMNS, SESSION_MINUTES, candle_times)
from marketlens.broker.dhan_data import (CHAIN_COLUMNS, QUOTE_COLUMNS, OptionChain,
                                         check_expired_request)
from marketlens.broker.dhan_instruments import INDICES, Security
from marketlens.logger import get_logger
from marketlens.options import expiries
from marketlens.options.pricing import RISK_FREE_RATE, bs_greeks, bs_price
from marketlens.utils import retry

log = get_logger(__name__)

LABEL = "DEMO DATA - made up for practice, not real prices"
YF_TICKERS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
              "SENSEX": "^BSESN"}
DEFAULT_SPOTS = {"NIFTY": 25000.0, "BANKNIFTY": 56000.0, "FINNIFTY": 26000.0, "SENSEX": 82000.0}
DEFAULT_STOCK_PRICE = 1000.0
DEFAULT_VIX = 14.0
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "SENSEX": 100}
STRIKES_EACH_SIDE = 20
# The repeatable past price path: one value per weekday from 2015 to 2035
LEVEL_START, LEVEL_END = dt.date(2015, 1, 1), dt.date(2035, 12, 31)
LEVEL_ANCHOR_DAY = dt.date(2026, 1, 1)   # the path equals the anchor price on this day
DEMO_CAPITAL_RS = 10_00_000       # virtual money shown as the DEMO fund balance
DEMO_MARGIN_PCT = 0.12            # rough SPAN + exposure margin for sold options / futures
TICK = 0.05


# Latest real close from yfinance (tried twice), or None when offline.
@retry(retries=1, first_wait=0.5)
def _yf_last_close(ticker: str) -> float:
    data = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
    if data is None or data.empty:
        raise ConnectionError(f"no yfinance data for {ticker}")
    close = data["Close"]
    close = close.iloc[:, 0] if isinstance(close, pd.DataFrame) else close
    return float(close.dropna().iloc[-1])


# Same as above, remembered for the day so pages stay fast.
@lru_cache(maxsize=64)
def _cached_close(ticker: str, day: dt.date) -> float | None:
    try:
        return _yf_last_close(ticker)
    except Exception as err:
        log.warning("DEMO data: could not get %s from yfinance (%s); using a default", ticker, err)
        return None


# Round a price to NSE's 5-paise tick (never below 0.05).
def to_tick(price: float) -> float:
    return max(TICK, round(round(price / TICK) * TICK, 2))


# The expiry an option traded on `day` belongs to (weekly if that existed, else monthly).
def next_expiry(underlying: str, day: dt.date, weekly: bool) -> dt.date:
    """Uses the dated exchange rules in marketlens/options/expiries.py (holidays not moved)."""
    flag = expiries.WEEK if weekly and expiries.has_weekly(underlying, day) else expiries.MONTH
    return expiries.rule_expiry(underlying, day, flag)


# One close per weekday (2015-2035) for a symbol: the backbone of all DEMO past prices.
@lru_cache(maxsize=64)
def _daily_levels(symbol: str, anchor: float, vol: float) -> pd.Series:
    days = pd.bdate_range(LEVEL_START, LEVEL_END)
    rng = np.random.default_rng(zlib.crc32(f"levels|{symbol}".encode()))
    walk = np.cumsum(rng.normal(0, vol / math.sqrt(252), len(days)))
    start = days.searchsorted(pd.Timestamp(LEVEL_ANCHOR_DAY))
    return pd.Series(anchor * np.exp(walk - walk[start]), index=days.date)


# Years from a moment until 15:30 IST on the expiry day (at least one minute).
def years_to_expiry(now: dt.datetime, expiry: dt.date) -> float:
    close = dt.datetime.combine(expiry, MARKET_CLOSE, tzinfo=IST)
    return max((close - now).total_seconds(), 60) / (365 * 24 * 3600)


# Strike gap for an underlying: 50 for NIFTY, 100 for SENSEX, about 1% of price for stocks.
def strike_step(underlying: str, spot: float) -> float:
    if underlying.upper() in STRIKE_STEP:
        return STRIKE_STEP[underlying.upper()]
    for step in (1, 2.5, 5, 10, 20, 50, 100):
        if step >= spot * 0.005:
            return step
    return 100


# Volatility for one strike: VIX plus a small smile (far strikes and puts a bit higher).
def smile_vol(base: float, spot: float, strike: float) -> float:
    m = math.log(strike / spot)
    return max(0.05, base * (1 + 6 * m * m - 0.8 * m))


class DemoDataClient:
    """Fake but realistic data with the same methods as DhanDataClient."""
    source = "DEMO"
    is_demo = True
    label = LABEL

    # spots / vix: fixed values (the tests use these, so no internet is needed).
    def __init__(self, spots: dict | None = None, vix: float | None = None,
                 now: dt.datetime | None = None):
        self._spots = {k.upper(): float(v) for k, v in (spots or {}).items()}
        self._vix = vix
        self._now = now
        self._offline = spots is not None

    # The DEMO client always works.
    def is_ready(self) -> bool:
        return True

    # Current time in IST (fixed in tests).
    def now(self) -> dt.datetime:
        return self._now or dt.datetime.now(IST)

    # Spot price of an index or stock (yfinance close, or a default).
    def spot(self, symbol: str) -> float:
        symbol = symbol.upper()
        if symbol in self._spots:
            return self._spots[symbol]
        default = DEFAULT_SPOTS.get(symbol, DEFAULT_STOCK_PRICE)
        if self._offline:
            return default
        price = _cached_close(YF_TICKERS.get(symbol, f"{symbol}.NS"), self.now().date())
        return price or default

    # Market volatility as a fraction: India VIX / 100.
    def vol(self) -> float:
        if self._vix is not None:
            return self._vix / 100
        vix = None if self._offline else _cached_close("^INDIAVIX", self.now().date())
        return (vix or DEFAULT_VIX) / 100

    # Random numbers that repeat for the same inputs.
    @staticmethod
    def _rng(*parts) -> np.random.Generator:
        return np.random.default_rng(zlib.crc32("|".join(map(str, parts)).encode()))

    # Turn a name or Security into a Security (indices and stocks only need the name).
    @staticmethod
    def _security(item) -> Security:
        if isinstance(item, Security):
            return item
        name = str(item).upper()
        return INDICES.get(name) or Security(name, "DEMO", "NSE_EQ", "EQUITY", "NSE", "stock", name)

    # ---------------- market data ----------------

    # Latest DEMO price, day's OHLC and volume for several symbols.
    def get_quote(self, symbols: list) -> pd.DataFrame:
        rows = []
        for item in symbols:
            sec = self._security(item)
            spot = self.spot(sec.underlying or sec.symbol)
            rng = self._rng("quote", sec.symbol, self.now().date())
            prev = spot / (1 + rng.normal(0, self.vol() / math.sqrt(252)))
            day_move = abs(rng.normal(0, self.vol() / math.sqrt(252))) * spot
            rows.append({"symbol": sec.symbol, "security_id": sec.security_id, "ltp": round(spot, 2),
                         "open": round(prev, 2), "high": round(max(prev, spot) + day_move / 2, 2),
                         "low": round(min(prev, spot) - day_move / 2, 2), "close": round(prev, 2),
                         "change": round(spot - prev, 2),
                         "volume": int(rng.integers(100_000, 5_000_000)), "source": self.source})
        return pd.DataFrame(rows, columns=QUOTE_COLUMNS)

    # Upcoming expiries by the NSE/BSE rules (weekly where they exist, plus 3 monthlies).
    def get_expiry_list(self, underlying: str, weeks: int = 4, months: int = 3) -> list[dt.date]:
        underlying = underlying.upper()
        today = self.now().date()
        dates = set()
        if expiries.has_weekly(underlying, today):
            first = next_expiry(underlying, today, weekly=True)
            dates.update(first + dt.timedelta(weeks=k) for k in range(weeks))
        day = today
        for _ in range(months):
            expiry = next_expiry(underlying, day, weekly=False)
            dates.add(expiry)
            day = expiry + dt.timedelta(days=1)
        return sorted(dates)

    # A DEMO option chain from Black-Scholes, around the spot price.
    def get_option_chain(self, underlying: str, expiry: dt.date) -> OptionChain:
        underlying = underlying.upper()
        spot, base_vol, now = self.spot(underlying), self.vol(), self.now()
        step = strike_step(underlying, spot)
        atm = round(spot / step) * step
        years = years_to_expiry(now, expiry)
        rng = self._rng("chain", underlying, expiry, now.date())
        rows = []
        for k in range(-STRIKES_EACH_SIDE, STRIKES_EACH_SIDE + 1):
            strike = atm + k * step
            vol = smile_vol(base_vol, spot, strike)
            # Open interest: calls pile up above the spot, puts below; round strikes get more
            round_bonus = 1.6 if strike % (step * 2) == 0 else 1.0
            row = {"strike": float(strike)}
            for side, centre in (("ce", spot * 1.01), ("pe", spot * 0.99)):
                opt = side.upper()
                price = to_tick(bs_price(spot, strike, years, vol, opt))
                greeks = bs_greeks(spot, strike, years, vol, opt)
                oi = int(5_000_000 * round_bonus * math.exp(-((strike - centre) / (spot * 0.025)) ** 2)
                         * rng.uniform(0.7, 1.3)) + int(rng.integers(5_000, 50_000))
                spread = max(TICK, to_tick(price * 0.002))
                row.update({
                    f"{side}_security_id": "DEMO", f"{side}_ltp": price,
                    f"{side}_bid": max(TICK, round(price - spread, 2)), f"{side}_ask": round(price + spread, 2),
                    f"{side}_oi": oi, f"{side}_oi_change": int(oi * rng.uniform(-0.2, 0.3)),
                    f"{side}_volume": int(oi * rng.uniform(1, 6)), f"{side}_iv": round(vol * 100, 2),
                    f"{side}_delta": round(greeks["delta"], 4), f"{side}_gamma": round(greeks["gamma"], 6),
                    f"{side}_theta": round(greeks["theta"], 2), f"{side}_vega": round(greeks["vega"], 2),
                })
            rows.append(row)
        return OptionChain(underlying, expiry, spot, pd.DataFrame(rows, columns=CHAIN_COLUMNS), self.source)

    # Volatility used for DEMO past prices: the given VIX, else a fixed 14% (never live data,
    # so stored DEMO history never changes from one day to the next).
    def _history_vol(self) -> float:
        return (self._vix if self._vix is not None else DEFAULT_VIX) / 100

    # Close of `symbol` on every weekday (the shared, repeatable DEMO path).
    def _levels(self, symbol: str) -> pd.Series:
        symbol = symbol.upper()
        anchor = self._spots.get(symbol) or DEFAULT_SPOTS.get(symbol, DEFAULT_STOCK_PRICE)
        return _daily_levels(symbol, anchor, self._history_vol())

    # The DEMO price of `symbol` at each candle time (same answer for every strike and module).
    def _path(self, symbol: str, times: list, minutes: int) -> np.ndarray:
        """Daily: that day's close. Intraday: a walk from the previous close to the day's close."""
        levels = self._levels(symbol)
        out = np.empty(len(times))
        by_day = {}
        for i, t in enumerate(times):
            by_day.setdefault(t.date(), []).append(i)
        for day, idx in by_day.items():
            pos = levels.index.get_loc(day)
            close, prev = levels.iloc[pos], levels.iloc[max(pos - 1, 0)]
            if minutes == DAILY_MINUTES:
                out[idx] = close
                continue
            rng = self._rng("path", symbol.upper(), day, minutes)
            step = self._history_vol() * math.sqrt(minutes / (252 * SESSION_MINUTES))
            walk = np.cumsum(rng.normal(0, step, len(idx)))
            frac = np.arange(1, len(idx) + 1) / len(idx)
            # Brownian bridge: random wiggles that still end exactly at the day's close
            out[idx] = prev * np.exp(walk - frac * walk[-1] + frac * math.log(close / prev))
        return out

    # Open/high/low around a closing-price series.
    @staticmethod
    def _bars(close: np.ndarray, rng, wiggle: float) -> dict:
        open_ = np.r_[close[0], close[:-1]]
        extra = np.abs(rng.normal(0, wiggle, len(close))) * close
        return {"open": open_, "high": np.maximum(open_, close) + extra,
                "low": np.maximum(TICK, np.minimum(open_, close) - extra), "close": close}

    # DEMO candles for an index, stock, future or option (minutes 1/5/15/25/60, or 1440 = daily).
    def get_candles(self, security, start: dt.date, end: dt.date, minutes: int,
                    progress=None) -> pd.DataFrame:
        sec = self._security(security)
        times = candle_times(start, end, minutes)
        if progress:
            progress(1, 1)
        if not times:
            return pd.DataFrame(columns=COLUMNS)
        rng = self._rng("candles", sec.security_id, sec.symbol, start, minutes)
        under = self._path(sec.underlying or sec.symbol, times, minutes)
        if sec.kind == "option":
            years = [years_to_expiry(t.to_pydatetime(), sec.expiry) for t in times]
            vol = self._history_vol()
            close = np.array([bs_price(s, sec.strike, y, smile_vol(vol, s, sec.strike), sec.option_type)
                              for s, y in zip(under, years)])
        elif sec.kind == "future":
            years = np.array([years_to_expiry(t.to_pydatetime(), sec.expiry) for t in times])
            close = under * np.exp(RISK_FREE_RATE * years)        # futures trade a little above spot
        else:
            close = under
        bars = self._bars(close, rng, 0.0005 if minutes != DAILY_MINUTES else 0.004)
        df = pd.DataFrame({"timestamp": times, **{k: np.round(v, 2) for k, v in bars.items()}})
        df["volume"] = rng.integers(1_000, 100_000, len(df))
        df["oi"] = rng.integers(100_000, 2_000_000, len(df)) if sec.kind in ("future", "option") else None
        df["source"] = self.source
        return df[COLUMNS]

    # DEMO intraday candles (1, 5, 15, 25 or 60 minutes).
    def get_intraday(self, security, start: dt.date, end: dt.date, interval: int = 1,
                     progress=None) -> pd.DataFrame:
        if interval not in INTRADAY_INTERVALS:
            raise ValueError(f"interval must be one of {INTRADAY_INTERVALS}")
        return self.get_candles(security, start, end, interval, progress)

    # DEMO expired options by position (ATM, ATM+1 ...): the strike follows the spot, like Dhan's data.
    def get_expired_options(self, underlying: str, expiry_flag: str, strike: str, option_type: str,
                            start: dt.date, end: dt.date, interval: int = 1, expiry_code: int = 1,
                            progress=None) -> pd.DataFrame:
        flag, offset, side = check_expired_request(underlying, expiry_flag, strike, option_type, interval)
        underlying = underlying.upper()
        opt = "CE" if side == "CALL" else "PE"
        times = candle_times(start, end, interval)
        if progress:
            progress(1, 1)
        if not times:
            return pd.DataFrame(columns=OPTION_COLUMNS)
        if flag == expiries.WEEK and not expiries.has_weekly(underlying, start):
            raise ValueError(f"{underlying} had no weekly options on {start:%d-%b-%Y}; use monthly.")
        rng = self._rng("expired", underlying, flag, strike, opt, start, interval)
        spots = self._path(underlying, times, interval)
        step = strike_step(underlying, float(spots[0]))
        strikes, ivs, closes = [], [], []
        for t, s in zip(times, spots):
            k = round(s / step) * step + offset * step
            expiry = expiries.rule_expiry(underlying, t.date(), flag)
            vol = smile_vol(self._history_vol(), s, k)
            strikes.append(float(k))
            ivs.append(round(vol * 100, 2))
            closes.append(bs_price(s, k, years_to_expiry(t.to_pydatetime(), expiry), vol, opt))
        bars = self._bars(np.array(closes), rng, 0.002)
        df = pd.DataFrame({"timestamp": times, **{k: np.round(v, 2) for k, v in bars.items()}})
        df["volume"] = rng.integers(10_000, 2_000_000, len(df))
        df["oi"] = rng.integers(500_000, 10_000_000, len(df))
        df["iv"], df["strike"], df["spot"] = ivs, strikes, np.round(spots, 2)
        df["source"] = self.source
        return df[OPTION_COLUMNS]

    # ---------------- account (virtual) ----------------

    # Rough DEMO margin: sold options/futures ~12% of value, bought options = premium paid.
    def get_margin(self, legs: list[dict]) -> dict:
        total, details = 0.0, []
        for leg in legs:
            sec = self._security(leg["security"])
            qty, price = int(leg["qty"]), float(leg.get("price", 0))
            spot = self.spot(sec.underlying or sec.symbol)
            selling = leg["side"].upper() == "SELL"
            if sec.kind == "option" and not selling:
                need = price * qty
            elif sec.kind in ("option", "future") or leg.get("product") != "CNC":
                need = DEMO_MARGIN_PCT * spot * qty
            else:
                need = (price or spot) * qty
            total += need
            details.append({"symbol": sec.symbol, "side": leg["side"].upper(), "qty": qty,
                            "margin": round(need, 2)})
        return {"source": self.source, "total_margin": round(total, 2), "details": details,
                "note": "Rough DEMO estimate; Dhan's margin calculator gives the real number."}

    # No real holdings in DEMO mode.
    def get_holdings(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", "qty", "avg_price", "source"])

    # No real positions in DEMO mode (paper trades are tracked by the paper broker).
    def get_positions(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", "qty", "avg_price", "pnl", "source"])

    # Virtual money for practice.
    def get_fund_limits(self) -> dict:
        return {"source": self.source, "available_balance": float(DEMO_CAPITAL_RS),
                "utilized_amount": 0.0, "note": "Virtual DEMO money"}
