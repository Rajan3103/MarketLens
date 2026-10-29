"""Option chain maths for the Option Chain tab (module 2). Plain tables in, numbers out.

The chain table comes from get_data_client().get_option_chain(...).table and has one row
per strike: strike, ce_ltp, ce_oi, ce_oi_change, ce_iv, ce_delta, ... and the same for pe_.

    atm_strike(table, spot)      strike nearest the spot
    pcr(table)                   put-call ratio = total put OI / total call OI
    max_pain(table)              expiry price at which option buyers would get the least
    atm_straddle(table, spot)    ATM call price + ATM put price
    expected_move(spot, iv, days) one-standard-deviation move to expiry
"""
import datetime as dt
import math

import pandas as pd

from marketlens.logger import get_logger

log = get_logger(__name__)

# Lot sizes if Dhan's instrument list is not available (checked 24-Oct-2026; they change)
DEFAULT_LOT_SIZES = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60, "SENSEX": 20}
# Columns shown on the page, in order: calls | strike | puts
VIEW_COLUMNS = ["ce_oi", "ce_oi_change", "ce_iv", "ce_delta", "ce_ltp", "strike",
                "pe_ltp", "pe_delta", "pe_iv", "pe_oi_change", "pe_oi"]
VIEW_LABELS = ["Call OI", "Call OI chg", "Call IV", "Call delta", "Call LTP", "Strike",
               "Put LTP", "Put delta", "Put IV", "Put OI chg", "Put OI"]


# The strike nearest the spot price (at the money).
def atm_strike(table: pd.DataFrame, spot: float) -> float:
    return float(table["strike"].iloc[(table["strike"] - spot).abs().argmin()])


# Put-call ratio: total put open interest divided by total call open interest.
def pcr(table: pd.DataFrame) -> float | None:
    """Above 1: more puts open (often read as support / bullish); below 1: more calls. None if no call OI."""
    calls = table["ce_oi"].fillna(0).sum()
    return round(float(table["pe_oi"].fillna(0).sum() / calls), 2) if calls > 0 else None


# Max pain: the expiry price where option BUYERS would collect the least money in total.
def max_pain(table: pd.DataFrame) -> float | None:
    """For each candidate expiry price P: calls pay OI x max(P - K, 0), puts pay OI x max(K - P, 0).
    Max pain = the P (one of the strikes) with the smallest total. None without OI."""
    strikes = table["strike"].to_numpy(dtype=float)
    ce_oi = table["ce_oi"].fillna(0).to_numpy(dtype=float)
    pe_oi = table["pe_oi"].fillna(0).to_numpy(dtype=float)
    if ce_oi.sum() + pe_oi.sum() == 0:
        return None
    totals = [(ce_oi * (p - strikes).clip(min=0)).sum() + (pe_oi * (strikes - p).clip(min=0)).sum()
              for p in strikes]
    return float(strikes[int(pd.Series(totals).idxmin())])


# Payout to option buyers at every candidate expiry price (for the max pain chart).
def pain_curve(table: pd.DataFrame) -> pd.DataFrame:
    strikes = table["strike"].to_numpy(dtype=float)
    ce_oi = table["ce_oi"].fillna(0).to_numpy(dtype=float)
    pe_oi = table["pe_oi"].fillna(0).to_numpy(dtype=float)
    rows = [{"price": p, "calls": (ce_oi * (p - strikes).clip(min=0)).sum(),
             "puts": (pe_oi * (strikes - p).clip(min=0)).sum()} for p in strikes]
    out = pd.DataFrame(rows)
    out["total"] = out["calls"] + out["puts"]
    return out


# Price of the ATM straddle: buying the ATM call and the ATM put together.
def atm_straddle(table: pd.DataFrame, spot: float) -> float | None:
    row = table[table["strike"] == atm_strike(table, spot)].iloc[0]
    if pd.isna(row["ce_ltp"]) or pd.isna(row["pe_ltp"]):
        return None
    return float(row["ce_ltp"] + row["pe_ltp"])


# Average implied volatility (in %) of the ATM call and put.
def atm_iv(table: pd.DataFrame, spot: float) -> float | None:
    row = table[table["strike"] == atm_strike(table, spot)].iloc[0]
    values = [v for v in (row["ce_iv"], row["pe_iv"]) if pd.notna(v) and v > 0]
    return float(sum(values) / len(values)) if values else None


# Days from now until 15:30 IST on the expiry day (at least a few minutes).
def days_to_expiry(expiry: dt.date, now: dt.datetime | None = None) -> float:
    from marketlens.broker.candles import IST, MARKET_CLOSE
    now = now or dt.datetime.now(IST)
    close = dt.datetime.combine(expiry, MARKET_CLOSE, tzinfo=IST)
    return max((close - now).total_seconds() / 86400, 5 / 1440)


# Expected move to expiry: spot x IV x sqrt(days / 365), about one standard deviation.
def expected_move(spot: float, iv_pct: float | None, days: float, straddle: float | None = None) -> float | None:
    """Roughly 68% of the time the price stays within spot +/- this amount by expiry.
    If IV is missing, 0.8 x the ATM straddle price is used (a common rule of thumb)."""
    if iv_pct:
        return spot * iv_pct / 100 * math.sqrt(days / 365)
    return 0.8 * straddle if straddle else None


# The strikes around the ATM strike, with in-the-money flags for the page's shading.
def chain_view(table: pd.DataFrame, spot: float, width: int = 10) -> pd.DataFrame:
    """width strikes above and below ATM. ce_itm: call in the money (strike below spot);
    pe_itm: put in the money (strike above spot)."""
    pos = int((table["strike"] - spot).abs().argmin())
    view = table.iloc[max(0, pos - width): pos + width + 1].copy()
    view["ce_itm"] = view["strike"] < spot
    view["pe_itm"] = view["strike"] > spot
    view["is_atm"] = view["strike"] == atm_strike(table, spot)
    return view.reset_index(drop=True)


# All the numbers for the cards at the top of the tab.
def chain_summary(table: pd.DataFrame, spot: float, expiry: dt.date,
                  now: dt.datetime | None = None) -> dict:
    straddle = atm_straddle(table, spot)
    days = days_to_expiry(expiry, now)
    iv = atm_iv(table, spot)
    move = expected_move(spot, iv, days, straddle)
    return {"spot": spot, "atm": atm_strike(table, spot), "pcr": pcr(table), "max_pain": max_pain(table),
            "straddle": straddle, "atm_iv": iv, "days": days, "expected_move": move,
            "expected_move_pct": move / spot * 100 if move else None}


# Lot size of an index's options: Dhan's instrument list, or the defaults above when offline.
def lot_size_for(underlying: str) -> int:
    try:
        from marketlens.broker.dhan_instruments import lot_size
        found = lot_size(underlying)
        if found:
            return found
    except Exception as err:
        log.warning("Lot size from Dhan's list not available (%s); using the default", err)
    return DEFAULT_LOT_SIZES.get(underlying.upper(), 1)


# A strategy-builder leg from one row of the chain (premium = last traded price, with its IV).
def make_leg(table: pd.DataFrame, underlying: str, expiry: dt.date, strike: float, option_type: str,
             side: str = "BUY", lots: int = 1, lot_size: int = 1):
    """Returns a strategies.Leg. Raises ValueError if the strike is not in the chain."""
    from marketlens.options.strategies import Leg
    option_type = option_type.upper()
    if option_type not in ("CE", "PE"):
        raise ValueError("option_type must be CE or PE")
    rows = table[table["strike"] == float(strike)]
    if rows.empty:
        raise ValueError(f"Strike {strike} is not in this chain")
    row = rows.iloc[0]
    premium, iv = row[f"{option_type.lower()}_ltp"], row[f"{option_type.lower()}_iv"]
    return Leg(side, option_type, float(strike), expiry, int(lots),
               float(premium) if pd.notna(premium) else 0.0, int(lot_size),
               float(iv) if pd.notna(iv) else None)
