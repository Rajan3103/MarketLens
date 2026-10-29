"""Choosing strikes: shared by the Strategy builder (module 2) and the Options Backtester (module 4).

Every helper works on a "long" table: one row per strike and option type, with columns
    strike, option_type (CE/PE), price, iv (in %), delta
Turn the live chain into this shape with chain_to_long(); the backtester's stored minutes
(load_option_day) only need their 'close' column renamed to 'price'.

    atm_offset_strike(strikes, spot, +2)            -> two strikes above ATM
    strike_for_premium(options, "CE", 50)           -> call strike whose price is closest to Rs 50
    strike_for_delta(options, "PE", 0.20)           -> put strike whose delta is closest to -0.20
"""
import math

import pandas as pd

from marketlens.options.pricing import RISK_FREE_RATE, greeks

LONG_COLUMNS = ["strike", "option_type", "price", "iv", "delta", "oi"]


# The live chain (one row per strike, CE and PE side by side) -> one row per strike and type.
def chain_to_long(table: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for side in ("ce", "pe"):
        part = pd.DataFrame({"strike": table["strike"].astype(float), "option_type": side.upper(),
                             "price": table[f"{side}_ltp"], "iv": table[f"{side}_iv"],
                             "delta": table[f"{side}_delta"], "oi": table[f"{side}_oi"]})
        parts.append(part)
    return pd.concat(parts, ignore_index=True)[LONG_COLUMNS]


# The usual gap between strikes (e.g. 50 for NIFTY), from a list of strikes.
def strike_step(strikes) -> float:
    values = sorted(set(float(s) for s in strikes))
    gaps = pd.Series([b - a for a, b in zip(values, values[1:])])
    if gaps.empty:
        raise ValueError("Need at least two strikes to know the strike gap")
    return float(gaps.mode().min())


# The strike nearest the spot.
def atm(strikes, spot: float) -> float:
    values = sorted(set(float(s) for s in strikes))
    return min(values, key=lambda k: (abs(k - spot), k))


# ATM +/- n strikes: n = +2 is two strikes ABOVE the ATM strike, n = -2 two strikes below.
def atm_offset_strike(strikes, spot: float, n: int) -> float:
    """Raises ValueError if the chain does not reach that far."""
    values = sorted(set(float(s) for s in strikes))
    pos = values.index(atm(values, spot)) + int(n)
    if not 0 <= pos < len(values):
        raise ValueError(f"ATM{n:+d} is outside the strikes available ({values[0]:,.0f} to {values[-1]:,.0f})")
    return values[pos]


# How many strikes a strike is away from ATM (the reverse of atm_offset_strike).
def offset_of(strike: float, strikes, spot: float) -> int:
    values = sorted(set(float(s) for s in strikes))
    return values.index(float(strike)) - values.index(atm(values, spot))


# Rows of one option type with a usable price.
def _side(options: pd.DataFrame, option_type: str) -> pd.DataFrame:
    option_type = option_type.strip().upper()
    if option_type not in ("CE", "PE"):
        raise ValueError("option_type must be CE or PE")
    rows = options[(options["option_type"] == option_type) & options["price"].notna() & (options["price"] > 0)]
    if rows.empty:
        raise ValueError(f"No {option_type} prices available")
    return rows


# The strike whose option price is closest to a target premium (e.g. Rs 50).
def strike_for_premium(options: pd.DataFrame, option_type: str, target: float) -> float:
    """Ties go to the strike further from the money (the cheaper side of the target)."""
    rows = _side(options, option_type)
    gap = (rows["price"] - target).abs()
    best = rows[gap == gap.min()]
    far = best["strike"].max() if option_type.upper() == "CE" else best["strike"].min()
    return float(far)


# Add a delta column from IV with Black-Scholes where it is missing (stored minutes have no delta).
def fill_delta(options: pd.DataFrame, spot: float, years: float, r: float = RISK_FREE_RATE) -> pd.DataFrame:
    out = options.copy()
    if "delta" not in out:
        out["delta"] = math.nan
    missing = out["delta"].isna() & out["iv"].notna() & (out["iv"] > 0)
    out.loc[missing, "delta"] = [greeks(spot, k, years, r, iv / 100, t)["delta"]
                                 for k, iv, t in out.loc[missing, ["strike", "iv", "option_type"]].to_numpy()]
    return out


# The strike whose delta is closest to a target (0.20 means +0.20 for calls, -0.20 for puts).
def strike_for_delta(options: pd.DataFrame, option_type: str, target: float, spot: float | None = None,
                     years: float | None = None) -> float:
    """Missing deltas are worked out from IV when spot and years are given."""
    if spot is not None and years is not None:
        options = fill_delta(options, spot, years)
    rows = _side(options, option_type)
    rows = rows[rows["delta"].notna()]
    if rows.empty:
        raise ValueError("No deltas available: give spot and years so they can be worked out from IV")
    gap = (rows["delta"].abs() - abs(target)).abs()
    return float(rows.loc[gap.idxmin(), "strike"])
