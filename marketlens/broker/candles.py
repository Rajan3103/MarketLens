"""Small helpers shared by the Dhan and DEMO data clients: candle columns,
Indian market hours, splitting date ranges and reading Dhan's candle answers."""
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi", "source"]
OPTION_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi", "iv", "strike",
                  "spot", "source"]
DAILY_MINUTES = 1440                     # "timeframe" of a daily candle
INTRADAY_INTERVALS = (1, 5, 15, 25, 60)  # minutes Dhan supports
MARKET_OPEN, MARKET_CLOSE = dt.time(9, 15), dt.time(15, 30)
SESSION_MINUTES = 375                    # 09:15 to 15:30


# Split a date range into pieces of at most `days` days (both ends included).
def date_chunks(start: dt.date, end: dt.date, days: int) -> list[tuple]:
    """1-Jan to 30-Apr with 90 days -> [(1-Jan, 31-Mar), (1-Apr, 30-Apr)]: no gaps, no overlaps."""
    chunks, first = [], start
    while first <= end:
        last = min(first + dt.timedelta(days=days - 1), end)
        chunks.append((first, last))
        first = last + dt.timedelta(days=1)
    return chunks


# Start times of every candle between two dates (weekdays, 09:15-15:30 IST).
def candle_times(start: dt.date, end: dt.date, minutes: int) -> list[pd.Timestamp]:
    """Daily candles get one time per weekday (midnight IST). Holidays are not removed."""
    days = pd.bdate_range(start, end)
    if minutes == DAILY_MINUTES:
        return [pd.Timestamp(d.date(), tz=IST) for d in days]
    per_day = int(np.ceil(SESSION_MINUTES / minutes))  # 60 min -> 7 candles, last one at 15:15
    return [pd.Timestamp(dt.datetime.combine(d.date(), MARKET_OPEN), tz=IST)
            + pd.Timedelta(minutes=minutes * k) for d in days for k in range(per_day)]


# Turn Dhan's answer (lists of numbers) into a table with IST times.
def to_frame(data: dict, columns: list[str] = COLUMNS, source: str = "dhan") -> pd.DataFrame:
    """Dhan sends lists: open/high/low/close/volume/timestamp (epoch seconds), and for
    options also oi/iv/strike/spot. 'open_interest' is renamed to 'oi'."""
    if not data or not data.get("timestamp"):
        return pd.DataFrame(columns=columns)
    n = len(data["timestamp"])
    df = pd.DataFrame({"timestamp": pd.to_datetime(data["timestamp"], unit="s", utc=True).tz_convert(IST)})
    for col in columns:
        if col in ("timestamp", "source"):
            continue
        values = data.get(col) if col != "oi" else (data.get("oi") or data.get("open_interest"))
        df[col] = values if values else [None] * n
    df["source"] = source
    return df[columns]
