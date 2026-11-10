"""Repair known errors in downloaded daily prices before a backtest uses them.

Yahoo Finance sometimes stores a few days of an Indian ETF at 1/10 or 1/100 of the real price
around a unit split (e.g. GOLDBEES 33.60 -> 0.34 -> 33.65 in Dec-2019). A backtest would see a
-99% crash and a +9,900% jump that never happened.

repair_scale_errors() walks forward through each column and compares every close with the
(repaired) close before it. A move of almost exactly 10x, 100x, 1/10 or 1/100 in one day is a
scale error, so that day is divided back by the same power of 10. Only past prices are used,
so the repair itself cannot look ahead. Real one-day moves are never that large (NSE circuit
limits are 20%), and normal splits (1:2, 1:5) are already adjusted by Yahoo.
"""
import math

import numpy as np
import pandas as pd

TOLERANCE = 0.1          # |log10(move) - whole number| below this = a power-of-10 error (x0.8 .. x1.26)


# The power of 10 a one-day move looks like (e.g. -2 for /100), or 0 for a normal move.
def scale_power(ratio: float) -> int:
    if not ratio > 0:
        return 0
    power = math.log10(ratio)
    whole = round(power)
    return int(whole) if whole != 0 and abs(power - whole) < TOLERANCE else 0


# Repair power-of-10 errors in close prices (rows = dates, columns = symbols).
def repair_scale_errors(closes: pd.DataFrame, others: dict | None = None) -> tuple[pd.DataFrame, dict, list]:
    """others: more tables with the same layout (e.g. {"open": opens}) fixed with the same factor.

    Returns (repaired closes, repaired others, list of fixes as dicts: symbol, date, factor).
    """
    closes = closes.sort_index().astype(float).copy()
    others = {name: frame.reindex_like(closes).astype(float).copy() for name, frame in (others or {}).items()}
    fixes = []
    for sym in closes.columns:
        values = closes[sym].to_numpy(copy=True)
        factors = np.ones(len(values))
        prev = math.nan
        for i, value in enumerate(values):
            if math.isnan(value):
                continue
            power = scale_power(value / prev) if not math.isnan(prev) else 0
            if power:
                factors[i] = 10.0 ** -power
                values[i] = value * factors[i]
                fixes.append({"symbol": sym, "date": closes.index[i].date(), "factor": factors[i]})
            prev = values[i]
        closes[sym] = values
        for frame in others.values():
            fixed = frame[sym].to_numpy(copy=True) * factors
            # an open still 10x away from its own day's close is the same error on that price only
            ratio = fixed / values
            for i, r in enumerate(ratio):
                power = scale_power(r) if not math.isnan(r) else 0
                if power:
                    fixed[i] *= 10.0 ** -power
            frame[sym] = fixed
    return closes, others, fixes


SPIKE_PCT = 12.0         # an index / ETF moving this much in one day and back the next day is a bad print


# Remove one-day bad prints from an index or ETF series (e.g. ^NSEI at 11,382 on 06-Apr-2020 when it was ~8,100).
def remove_spikes(series: pd.Series, spike_pct: float = SPIKE_PCT) -> tuple[pd.Series, list]:
    """A close is a bad print when it moved more than spike_pct % from the day before AND the next close takes back
    more than half of that move (up then down, or down then up). It is replaced by the day before's close.
    Returns (clean series, list of fixes: date, bad value, value used). For indices and ETFs only: stocks can
    really move 20 % (circuit limits), so they are left alone."""
    clean = series.astype(float).copy()
    values = clean.to_numpy(copy=True)
    fixes = []
    limit = spike_pct / 100
    for i in range(1, len(values) - 1):
        before, now, after = values[i - 1], values[i], values[i + 1]
        if math.isnan(before) or math.isnan(now) or math.isnan(after) or before <= 0 or now <= 0:
            continue
        move_in, move_out = now / before - 1, after / now - 1
        undone = abs(after / before - 1) < 0.5 * min(abs(move_in), abs(move_out))
        if abs(move_in) > limit and abs(move_out) > limit and move_in * move_out < 0 and undone:
            fixes.append({"date": clean.index[i].date(), "bad": round(float(now), 4), "used": round(float(before), 4)})
            values[i] = before
    clean[:] = values
    return clean, fixes
