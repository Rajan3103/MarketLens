"""What is stored in the database: one row per instrument and kind of data.

    what_is_stored()  -> table with: data, instrument, timeframe, first, last, rows, demo
Shown at the bottom of the module 3 page, so you can see what is ready for the
backtester, simulator and momentum engines before running them.
"""
import pandas as pd
from sqlalchemy import func, select

from marketlens.db import get_session
from marketlens.models import (DailyPrice, Instrument, IntradayOptionPrice, OptionContract, OptionQuote,
                               PriceBar)

COLUMNS = ["data", "instrument", "timeframe", "first", "last", "rows", "demo"]
TIMEFRAME_LABEL = {1: "1 min", 5: "5 min", 15: "15 min", 25: "25 min", 60: "60 min", 1440: "1 day"}


# Show a date-time as a plain date (the summary only needs days).
def _day(value):
    return value.date() if hasattr(value, "date") else value


# One row per instrument for each kind of stored data, newest data kinds first.
def what_is_stored(url: str | None = None) -> pd.DataFrame:
    rows = []
    with get_session(url) as session:
        # Free daily prices (yfinance)
        for symbol, kind, first, last, n in session.execute(
                select(Instrument.symbol, Instrument.instrument_type, func.min(DailyPrice.date),
                       func.max(DailyPrice.date), func.count())
                .join(Instrument, Instrument.id == DailyPrice.instrument_id)
                .group_by(Instrument.symbol, Instrument.instrument_type)):
            rows.append(("Daily prices (yfinance)", f"{symbol} ({kind.lower()})", "1 day",
                         first, last, n, False))

        # Dhan candles: index minutes, stock / futures candles
        for symbol, tf, demo, first, last, n in session.execute(
                select(Instrument.symbol, PriceBar.timeframe, PriceBar.is_demo, func.min(PriceBar.ts),
                       func.max(PriceBar.ts), func.count())
                .join(Instrument, Instrument.id == PriceBar.instrument_id)
                .group_by(Instrument.symbol, PriceBar.timeframe, PriceBar.is_demo)):
            rows.append(("Candles (Dhan)", symbol, TIMEFRAME_LABEL.get(tf, f"{tf} min"),
                         _day(first), _day(last), n, demo))

        # Expired / historical option minute bars
        for symbol, demo, first, last, n in session.execute(
                select(Instrument.symbol, OptionContract.is_demo, func.min(IntradayOptionPrice.ts),
                       func.max(IntradayOptionPrice.ts), func.count())
                .join(OptionContract, OptionContract.id == IntradayOptionPrice.contract_id)
                .join(Instrument, Instrument.id == OptionContract.underlying_id)
                .group_by(Instrument.symbol, OptionContract.is_demo)):
            rows.append(("Option minute bars", f"{symbol} options", "1 min", _day(first), _day(last),
                         n, demo))

        # Option chain snapshots
        for symbol, demo, first, last, n in session.execute(
                select(Instrument.symbol, OptionContract.is_demo, func.min(OptionQuote.ts),
                       func.max(OptionQuote.ts), func.count())
                .join(OptionContract, OptionContract.id == OptionQuote.contract_id)
                .join(Instrument, Instrument.id == OptionContract.underlying_id)
                .group_by(Instrument.symbol, OptionContract.is_demo)):
            rows.append(("Option chain snapshots", f"{symbol} options", "snapshot", _day(first),
                         _day(last), n, demo))
    return pd.DataFrame(rows, columns=COLUMNS)
