"""Market data and account details from Dhan, through the official dhanhq library (2.3).

DhanDataClient and DemoDataClient (demo_data.py) have the SAME methods and give
answers in the SAME shape, so pages never need to know which one they use.
Get the right one with marketlens.broker.market_data.get_data_client().

dhanhq functions used (checked in dhanhq 2.3.0):
  quote_data, expiry_list, option_chain, intraday_minute_data, historical_daily_data,
  expired_options_data (Dhan's "rolling option" endpoint), margin_calculator_multi,
  get_holdings, get_positions, get_fund_limits
"""
import datetime as dt
import re
from dataclasses import dataclass

import pandas as pd

from marketlens.broker.candles import (COLUMNS, DAILY_MINUTES, INTRADAY_INTERVALS, OPTION_COLUMNS,
                                       date_chunks, to_frame)
from marketlens.broker.dhan_client import (DhanError, DhanTemporaryError, get_dhan, has_keys,
                                           unwrap)
from marketlens.broker.dhan_instruments import FUTURES_EXCHANGE, INDICES, Security, resolve
from marketlens.logger import get_logger
from marketlens.utils import RateLimiter, retry

log = get_logger(__name__)

# Dhan's speed limits (data APIs ~5 per second; option chain / expiry list 1 every 3 seconds)
DATA_LIMITER = RateLimiter(5)
CHAIN_LIMITER = RateLimiter(1, period=3.0)
MAX_INTRADAY_DAYS = 90          # Dhan: intraday candles, 90 days per request
MAX_EXPIRED_OPTION_DAYS = 30    # Dhan: expired options, 30 days per request
EXPIRY_FLAGS = {"weekly": "WEEK", "monthly": "MONTH"}
OPTION_SIDES = {"CE": "CALL", "PE": "PUT", "CALL": "CALL", "PUT": "PUT"}
# Columns of every option chain table (Dhan and DEMO)
CHAIN_FIELDS = ["security_id", "ltp", "bid", "ask", "oi", "oi_change", "volume", "iv",
                "delta", "gamma", "theta", "vega"]
CHAIN_COLUMNS = ["strike"] + [f"ce_{f}" for f in CHAIN_FIELDS] + [f"pe_{f}" for f in CHAIN_FIELDS]
QUOTE_COLUMNS = ["symbol", "security_id", "ltp", "open", "high", "low", "close", "volume", "source"]


@dataclass
class OptionChain:
    """One option chain: the spot price and a table with one row per strike (CE and PE side by side)."""
    underlying: str
    expiry: dt.date
    spot: float
    table: pd.DataFrame          # columns = CHAIN_COLUMNS
    source: str                  # "DHAN" or "DEMO"

    # The strike nearest to the spot price (at the money).
    @property
    def atm_strike(self) -> float:
        strikes = self.table["strike"]
        return float(strikes.iloc[(strikes - self.spot).abs().argmin()])


# Read "ATM", "ATM+2", "ATM-1" -> 0, 2, -1, and check Dhan's limits.
def parse_strike(strike: str, underlying: str) -> int:
    """Index options: up to ATM+10 / ATM-10. Stock options: up to ATM+3 / ATM-3."""
    match = re.fullmatch(r"ATM(?:([+-])(\d+))?", strike.strip().upper())
    if not match:
        raise ValueError("strike must look like ATM, ATM+1 or ATM-2")
    offset = int(match.group(2) or 0) * (-1 if match.group(1) == "-" else 1)
    limit = 10 if underlying.upper() in FUTURES_EXCHANGE else 3
    if abs(offset) > limit:
        raise ValueError(f"Dhan allows strikes from ATM-{limit} to ATM+{limit} for {underlying}")
    return offset


# Check the inputs of an expired-options request (shared by Dhan and DEMO).
def check_expired_request(underlying: str, expiry_flag: str, strike: str, option_type: str,
                          interval: int) -> tuple[str, int, str]:
    """Returns (WEEK/MONTH, strike offset, CALL/PUT) or raises a friendly ValueError."""
    flag = EXPIRY_FLAGS.get(expiry_flag.strip().lower())
    if not flag:
        raise ValueError("expiry_flag must be 'weekly' or 'monthly'")
    side = OPTION_SIDES.get(option_type.strip().upper())
    if not side:
        raise ValueError("option_type must be CE or PE")
    if interval not in INTRADAY_INTERVALS:
        raise ValueError(f"interval must be one of {INTRADAY_INTERVALS} minutes")
    return flag, parse_strike(strike, underlying), side


# Dhan sometimes wraps answers in {"data": ..., "status": "success"}; take out the inside.
def inner(payload):
    if isinstance(payload, dict) and "data" in payload and set(payload) <= {"data", "status", "remarks"}:
        return payload["data"]
    return payload


# Turn Dhan's option chain answer into a table (one row per strike).
def parse_option_chain(data: dict) -> tuple[float, pd.DataFrame]:
    """data = {"last_price": spot, "oc": {"25000.000000": {"ce": {...}, "pe": {...}}, ...}}."""
    rows = []
    for strike_text, sides in (data.get("oc") or {}).items():
        row = {"strike": float(strike_text)}
        for side in ("ce", "pe"):
            q = (sides or {}).get(side) or {}
            greeks = q.get("greeks") or {}
            oi = q.get("oi")
            row.update({
                f"{side}_security_id": str(q["security_id"]) if q.get("security_id") else None,
                f"{side}_ltp": q.get("last_price"), f"{side}_bid": q.get("top_bid_price"),
                f"{side}_ask": q.get("top_ask_price"), f"{side}_oi": oi,
                f"{side}_oi_change": (oi - q["previous_oi"]) if oi is not None and q.get("previous_oi") is not None else None,
                f"{side}_volume": q.get("volume"), f"{side}_iv": q.get("implied_volatility"),
                f"{side}_delta": greeks.get("delta"), f"{side}_gamma": greeks.get("gamma"),
                f"{side}_theta": greeks.get("theta"), f"{side}_vega": greeks.get("vega"),
            })
        rows.append(row)
    table = pd.DataFrame(rows, columns=CHAIN_COLUMNS).sort_values("strike").reset_index(drop=True)
    return float(data.get("last_price") or 0), table


# Turn Dhan's quote answer {"NSE_EQ": {"2885": {...}}} into a table.
def parse_quotes(data: dict, names: dict) -> pd.DataFrame:
    """names maps (segment, security_id) -> symbol so rows show RELIANCE, not 2885."""
    rows = []
    for segment, items in (data or {}).items():
        for sid, q in (items or {}).items():
            ohlc = q.get("ohlc") or {}
            rows.append({"symbol": names.get((segment, str(sid)), str(sid)), "security_id": str(sid),
                         "ltp": q.get("last_price"), "open": ohlc.get("open"), "high": ohlc.get("high"),
                         "low": ohlc.get("low"), "close": ohlc.get("close"), "volume": q.get("volume"),
                         "source": "DHAN"})
    return pd.DataFrame(rows, columns=QUOTE_COLUMNS)


class DhanDataClient:
    """Real market data and account details from Dhan. Needs the Dhan keys in .env."""
    source = "DHAN"
    is_demo = False

    # dhan: an existing dhanhq client (tests pass a fake one); master: instrument list for lookups.
    def __init__(self, dhan=None, master: pd.DataFrame | None = None):
        self._dhan = dhan if dhan is not None else (get_dhan() if has_keys() else None)
        self._master = master

    # True when the Dhan keys are set and the client could be made.
    def is_ready(self) -> bool:
        return self._dhan is not None

    # Every Dhan call goes through here: speed limit, error check, and @retry on busy/network errors.
    @retry(exceptions=(DhanTemporaryError,))
    def _call(self, limiter: RateLimiter, method: str, *args, **kwargs):
        if not self.is_ready():
            raise DhanError("Dhan key not set (DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN).")
        with limiter:
            return inner(unwrap(getattr(self._dhan, method)(*args, **kwargs)))

    # Turn "NIFTY", "RELIANCE" or a Security into a Security.
    def _security(self, item) -> Security:
        if isinstance(item, Security):
            return item
        sec = resolve(str(item), self._master)
        if sec is None:
            raise ValueError(f"Dhan does not list {item!r}")
        return sec

    # ---------------- market data ----------------

    # Latest price, day's OHLC and volume for several symbols in one request.
    def get_quote(self, symbols: list) -> pd.DataFrame:
        secs = [self._security(s) for s in symbols]
        request, names = {}, {}
        for sec in secs:
            request.setdefault(sec.segment, []).append(int(sec.security_id))
            names[(sec.segment, sec.security_id)] = sec.symbol
        return parse_quotes(self._call(DATA_LIMITER, "quote_data", request), names)

    # All live option expiry dates of an index or stock, nearest first.
    def get_expiry_list(self, underlying: str) -> list[dt.date]:
        sec = self._security(underlying)
        dates = self._call(CHAIN_LIMITER, "expiry_list", int(sec.security_id), sec.segment)
        return sorted(dt.date.fromisoformat(d) for d in dates or [])

    # Live option chain: price, OI, IV and Greeks for every strike of one expiry.
    def get_option_chain(self, underlying: str, expiry: dt.date) -> OptionChain:
        sec = self._security(underlying)
        data = self._call(CHAIN_LIMITER, "option_chain", int(sec.security_id), sec.segment,
                          expiry.isoformat())
        spot, table = parse_option_chain(data or {})
        return OptionChain(sec.symbol, expiry, spot, table, self.source)

    # Candles for one instrument: minutes = 1/5/15/25/60, or 1440 for daily.
    def get_candles(self, security, start: dt.date, end: dt.date, minutes: int,
                    progress=None) -> pd.DataFrame:
        """Long intraday ranges are fetched in 90-day pieces; Dhan leaves out to_date, so ask one day more."""
        sec = self._security(security)
        chunks = [(start, end)] if minutes == DAILY_MINUTES else date_chunks(start, end, MAX_INTRADAY_DAYS)
        oi = sec.kind in ("future", "option")
        pieces = []
        for i, (first, last) in enumerate(chunks, start=1):
            to_date = (last + dt.timedelta(days=1)).isoformat()
            if minutes == DAILY_MINUTES:
                data = self._call(DATA_LIMITER, "historical_daily_data", sec.security_id, sec.segment,
                                  sec.instrument, first.isoformat(), to_date, oi=oi)
            else:
                data = self._call(DATA_LIMITER, "intraday_minute_data", sec.security_id, sec.segment,
                                  sec.instrument, first.isoformat(), to_date, interval=minutes, oi=oi)
            pieces.append(to_frame(data, COLUMNS, source="dhan"))
            if progress:
                progress(i, len(chunks))
        return _clean(pieces, start, end, COLUMNS)

    # Intraday candles (1, 5, 15, 25 or 60 minutes).
    def get_intraday(self, security, start: dt.date, end: dt.date, interval: int = 1,
                     progress=None) -> pd.DataFrame:
        if interval not in INTRADAY_INTERVALS:
            raise ValueError(f"interval must be one of {INTRADAY_INTERVALS}")
        return self.get_candles(security, start, end, interval, progress)

    # Expired options by position (ATM, ATM+1 ...), using Dhan's rolling option endpoint.
    def get_expired_options(self, underlying: str, expiry_flag: str, strike: str, option_type: str,
                            start: dt.date, end: dt.date, interval: int = 1, expiry_code: int = 1,
                            progress=None) -> pd.DataFrame:
        """expiry_flag: weekly or monthly. strike: ATM, ATM+1, ATM-2 ...  option_type: CE or PE.
        expiry_code: which expiry counted from each date (Dhan's own example uses 1 = nearest;
        to be confirmed once the keys are added). Fetched in 30-day pieces."""
        flag, _, side = check_expired_request(underlying, expiry_flag, strike, option_type, interval)
        sec = self._security(underlying)
        segment = f"{FUTURES_EXCHANGE.get(sec.symbol, 'NSE')}_FNO"
        instrument = "OPTIDX" if sec.symbol in INDICES else "OPTSTK"
        chunks = date_chunks(start, end, MAX_EXPIRED_OPTION_DAYS)
        pieces = []
        for i, (first, last) in enumerate(chunks, start=1):
            data = self._call(DATA_LIMITER, "expired_options_data", sec.security_id, segment, instrument,
                              flag, expiry_code, strike.strip().upper(), side,
                              ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                              first.isoformat(), (last + dt.timedelta(days=1)).isoformat(), interval)
            leg = (data or {}).get("ce" if side == "CALL" else "pe") or {}
            pieces.append(to_frame(leg, OPTION_COLUMNS, source="dhan"))
            if progress:
                progress(i, len(chunks))
        return _clean(pieces, start, end, OPTION_COLUMNS)

    # ---------------- account ----------------

    # Margin needed for several legs together (hedges lower the margin).
    def get_margin(self, legs: list[dict]) -> dict:
        """Each leg: {"security": symbol or Security, "side": "BUY"/"SELL", "qty": units,
        "price": 0 for market, "product": "INTRADAY"/"MARGIN"/"CNC"}."""
        scrips = []
        for leg in legs:
            sec = self._security(leg["security"])
            scrips.append({"exchangeSegment": sec.segment, "transactionType": leg["side"].upper(),
                           "quantity": int(leg["qty"]), "productType": leg.get("product", "MARGIN"),
                           "securityId": sec.security_id, "price": float(leg.get("price", 0)),
                           "triggerPrice": float(leg.get("trigger_price", 0))})
        data = self._call(DATA_LIMITER, "margin_calculator_multi", scrips) or {}
        total = next((v for k, v in data.items() if "total" in k.lower() and "margin" in k.lower()), None)
        return {"source": self.source, "total_margin": total, "details": data}

    # Shares and ETFs in your Dhan demat account.
    def get_holdings(self) -> pd.DataFrame:
        df = pd.DataFrame(self._call(DATA_LIMITER, "get_holdings") or [])
        df["source"] = self.source
        return df

    # Today's open and closed positions in your Dhan account.
    def get_positions(self) -> pd.DataFrame:
        df = pd.DataFrame(self._call(DATA_LIMITER, "get_positions") or [])
        df["source"] = self.source
        return df

    # Money available, used and blocked in your Dhan account.
    def get_fund_limits(self) -> dict:
        return {"source": self.source, **(self._call(DATA_LIMITER, "get_fund_limits") or {})}


# Join pieces, keep only the chosen dates, drop repeats, oldest first.
def _clean(pieces: list, start: dt.date, end: dt.date, columns: list[str]) -> pd.DataFrame:
    pieces = [p for p in pieces if not p.empty]
    if not pieces:
        return pd.DataFrame(columns=columns)
    df = pd.concat(pieces, ignore_index=True)
    dates = df["timestamp"].dt.date
    df = df[(dates >= start) & (dates <= end)].drop_duplicates("timestamp").sort_values("timestamp")
    return df.reset_index(drop=True)
