"""Option data into PostgreSQL: chain snapshots, expired-option minute bars and index minutes.

    snapshot_option_chain("NIFTY")                       -> saves today's chain (LTP, OI, IV, Greeks)
    download_expired_options("NIFTY", start, end)        -> minute bars, ATM-10 .. ATM+10, CE and PE
    download_index_minutes("NIFTY", start, end)          -> NIFTY spot minute bars (price_bars, timeframe 1)
    load_option_day("NIFTY", day)                        -> one day, all stored strikes, one table

Long downloads are safe to stop: every 30-day piece is saved together with a note in
download_progress saying which days are finished, in ONE database transaction. Running
the same download again skips finished days and carries on where it stopped.

Without Dhan keys the DEMO client is used (made-up but consistent data, marked is_demo),
so the backtester and simulator can be built and tested. When real Dhan data is downloaded,
the DEMO rows for that underlying are deleted first, so the two never mix.
"""
import datetime as dt

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select, text

from marketlens.broker.candles import IST
from marketlens.broker.dhan_data import MAX_EXPIRED_OPTION_DAYS, MAX_INTRADAY_DAYS
from marketlens.broker.dhan_instruments import INDICES, remember_security
from marketlens.broker.market_data import get_data_client
from marketlens.db import get_session, upsert
from marketlens.logger import get_logger
from marketlens.models import (DownloadProgress, Instrument, IntradayOptionPrice, OptionContract,
                               OptionQuote, PriceBar)
from marketlens.options import expiries

log = get_logger(__name__)

EXPIRED_DATASET = "expired_options"
INDEX_DATASET = "index_minutes"
DEFAULT_STRIKE_RANGE = 10          # ATM-10 .. ATM+10 (Dhan's limit for index options)
DEMO_MESSAGE = "Dhan keys not set: using DEMO minute data"


# "ATM-10" ... "ATM" ... "ATM+10".
def strike_labels(width: int = DEFAULT_STRIKE_RANGE) -> list[str]:
    return [f"ATM{k:+d}" if k else "ATM" for k in range(-width, width + 1)]


# Get the data client and say so in the log when it is DEMO.
def _client(client=None):
    client = client or get_data_client()
    if client.is_demo:
        log.warning(DEMO_MESSAGE)
    return client


# Start and end of one day in Indian time (for "all rows of this day" queries).
def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time(0), tzinfo=IST)
    return start, start + dt.timedelta(days=1)


# The instruments row of an index or stock; created if it is new. Returns its id.
def _underlying_id(session, underlying: str) -> int:
    underlying = underlying.upper()
    if underlying in INDICES:
        return remember_security(session, INDICES[underlying])
    found = session.scalar(select(Instrument.id).where(Instrument.symbol == underlying,
                                                       Instrument.exchange == "NSE"))
    if found:
        return found
    inst = Instrument(symbol=underlying, exchange="NSE", instrument_type="EQUITY")
    session.add(inst)
    session.commit()
    return inst.id


# Delete DEMO rows for an underlying before real Dhan data is stored (they must never mix).
def purge_demo(session, underlying_id: int) -> None:
    removed = session.execute(delete(OptionContract).where(
        OptionContract.underlying_id == underlying_id, OptionContract.is_demo)).rowcount
    removed += session.execute(delete(PriceBar).where(
        PriceBar.instrument_id == underlying_id, PriceBar.is_demo)).rowcount
    session.execute(delete(DownloadProgress).where(DownloadProgress.key.like("DEMO|%")))
    session.commit()
    if removed:
        log.info("Deleted %d DEMO rows before saving real Dhan data", removed)


# Weekdays between start and end that are not marked finished yet.
def missing_days(session, dataset: str, key: str, start: dt.date, end: dt.date) -> list[dt.date]:
    done = set(session.scalars(select(DownloadProgress.day).where(
        DownloadProgress.dataset == dataset, DownloadProgress.key == key,
        DownloadProgress.day.between(start, end))))
    return [d.date() for d in pd.bdate_range(start, end) if d.date() not in done]


# Group missing days into runs of consecutive weekdays, each at most `max_days` long.
def day_runs(days: list[dt.date], max_days: int) -> list[tuple[dt.date, dt.date]]:
    """[Mon, Tue, Wed, next Mon] -> [(Mon, next Mon)] (weekends don't break a run);
    a gap of a missing working day starts a new run."""
    runs = []
    for day in sorted(days):
        if runs:
            first, last = runs[-1]
            next_weekday = (pd.Timestamp(last) + pd.offsets.BDay(1)).date()
            if day == next_weekday and (day - first).days < max_days:
                runs[-1] = (first, day)
                continue
        runs.append((day, day))
    return runs


# Mark days as finished (with how many rows each gave). Today is never marked: it is not over yet.
def _mark_done(session, dataset: str, key: str, first: dt.date, last: dt.date, counts: dict) -> None:
    today = dt.datetime.now(IST).date()
    rows = [{"dataset": dataset, "key": key, "day": d.date(), "rows": int(counts.get(d.date(), 0))}
            for d in pd.bdate_range(first, last) if d.date() < today]
    if rows:
        upsert(session, DownloadProgress, rows, ["dataset", "key", "day"])


# Store one piece of expired-option minute bars; returns {day: rows saved}.
def save_option_bars(session, underlying: str, underlying_id: int, df: pd.DataFrame,
                     expiry_flag: str, option_type: str, is_demo: bool) -> dict:
    """Works out each row's expiry (rules + holiday shift), creates the contracts,
    then upserts the minute bars into intraday_option_prices."""
    if df.empty:
        return {}
    df = df.copy()
    days = df["timestamp"].dt.date
    trading_days = set(days)
    expiry_of = {}
    for day in trading_days:
        flag = expiry_flag if expiries.has_weekly(underlying, day) or expiry_flag == expiries.MONTH \
            else expiries.MONTH
        expiry_of[day] = expiries.expiry_for(underlying, day, flag, trading_days)
    df["expiry"] = days.map(expiry_of)
    df["strike"] = df["strike"].astype(float)

    keys = df[["expiry", "strike"]].drop_duplicates()
    upsert(session, OptionContract, [
        {"underlying_id": underlying_id, "expiry": e, "strike": s, "option_type": option_type,
         "is_demo": is_demo} for e, s in keys.itertuples(index=False)
    ], ["underlying_id", "expiry", "strike", "option_type"])
    ids = {(e, float(s)): cid for cid, e, s in session.execute(
        select(OptionContract.id, OptionContract.expiry, OptionContract.strike).where(
            OptionContract.underlying_id == underlying_id, OptionContract.option_type == option_type,
            OptionContract.expiry.in_(set(df["expiry"]))))}

    rows = [{
        "contract_id": ids[(r.expiry, r.strike)], "ts": r.timestamp.to_pydatetime(),
        "open": round(float(r.open), 4), "high": round(float(r.high), 4),
        "low": round(float(r.low), 4), "close": round(float(r.close), 4),
        "volume": None if pd.isna(r.volume) else int(r.volume),
        "oi": None if pd.isna(r.oi) else int(r.oi),
        "iv": None if pd.isna(r.iv) else round(float(r.iv), 4),
    } for r in df.itertuples()]
    upsert(session, IntradayOptionPrice, rows, ["contract_id", "ts"])
    return days.value_counts().to_dict()


# Download expired-option minute bars for many strikes into intraday_option_prices.
def download_expired_options(underlying: str, start: dt.date, end: dt.date,
                             expiry_flag: str = "weekly", strikes: list[str] | None = None,
                             option_types: tuple = ("CE", "PE"), client=None, progress=None,
                             url: str | None = None) -> dict:
    """strikes: e.g. ["ATM-1", "ATM", "ATM+1"]; default ATM-10 .. ATM+10.
    progress: optional function(done, total, text) for a progress bar.
    Skips days already stored; safe to stop and run again."""
    underlying = underlying.upper()
    flag = expiries.WEEK if expiry_flag.lower() == "weekly" else expiries.MONTH
    if end < start:
        raise ValueError("'To date' is before 'From date'.")
    if flag == expiries.WEEK and not (expiries.has_weekly(underlying, start)
                                      and expiries.has_weekly(underlying, end)):
        raise ValueError(f"{underlying} did not have weekly options for the whole period "
                         f"{start:%d-%b-%Y} to {end:%d-%b-%Y}; choose monthly.")
    strikes = strikes or strike_labels()
    client = _client(client)
    summary = {"source": client.source, "pieces": 0, "rows": 0, "skipped_days": 0, "series": 0}
    with get_session(url) as session:
        underlying_id = _underlying_id(session, underlying)
        if not client.is_demo:
            purge_demo(session, underlying_id)
        plan = []
        for label in strikes:
            for opt in option_types:
                key = f"{client.source}|{underlying}|{flag}|{label}|{opt.upper()}|1"
                todo = missing_days(session, EXPIRED_DATASET, key, start, end)
                summary["skipped_days"] += len(pd.bdate_range(start, end)) - len(todo)
                plan.append((label, opt.upper(), key, day_runs(todo, MAX_EXPIRED_OPTION_DAYS)))
        summary["series"] = len(plan)
        total = sum(len(runs) for *_, runs in plan) or 1
        done = 0
        for label, opt, key, runs in plan:
            for first, last in runs:
                try:
                    df = client.get_expired_options(underlying, expiry_flag, label, opt, first, last, 1)
                    counts = save_option_bars(session, underlying, underlying_id, df, flag, opt,
                                              client.is_demo)
                    _mark_done(session, EXPIRED_DATASET, key, first, last, counts)
                    session.commit()           # bars + "done" note saved together
                except Exception:
                    session.rollback()         # nothing half-saved; run again to resume
                    raise
                done += 1
                summary["pieces"] += 1
                summary["rows"] += sum(counts.values())
                if progress:
                    progress(done, total, f"{label} {opt} {first} to {last}")
    log.info("Expired options %s %s %s to %s: %s", underlying, flag, start, end, summary)
    return summary


# Download index spot minute bars (NIFTY, BANKNIFTY, FINNIFTY, SENSEX) into price_bars.
def download_index_minutes(underlying: str, start: dt.date, end: dt.date, interval: int = 1,
                           client=None, progress=None, url: str | None = None) -> dict:
    """Stored in price_bars with timeframe = interval (1 = one-minute bars). Skips finished days."""
    underlying = underlying.upper()
    if underlying not in INDICES:
        raise ValueError(f"Index minutes are for {list(INDICES)}, not {underlying!r}")
    client = _client(client)
    sec = INDICES[underlying]
    key = f"{client.source}|{underlying}|{interval}"
    summary = {"source": client.source, "pieces": 0, "rows": 0, "skipped_days": 0}
    with get_session(url) as session:
        instrument_id = _underlying_id(session, underlying)
        if not client.is_demo:
            purge_demo(session, instrument_id)
        todo = missing_days(session, INDEX_DATASET, key, start, end)
        summary["skipped_days"] = len(pd.bdate_range(start, end)) - len(todo)
        runs = day_runs(todo, MAX_INTRADAY_DAYS)
        for i, (first, last) in enumerate(runs, start=1):
            try:
                df = client.get_intraday(sec, first, last, interval)
                rows = [{"instrument_id": instrument_id, "timeframe": interval,
                         "ts": r.timestamp.to_pydatetime(), "open": round(float(r.open), 4),
                         "high": round(float(r.high), 4), "low": round(float(r.low), 4),
                         "close": round(float(r.close), 4),
                         "volume": None if pd.isna(r.volume) else int(r.volume),
                         "is_demo": client.is_demo} for r in df.itertuples()]
                if rows:
                    upsert(session, PriceBar, rows, ["instrument_id", "timeframe", "ts"])
                counts = df["timestamp"].dt.date.value_counts().to_dict() if not df.empty else {}
                _mark_done(session, INDEX_DATASET, key, first, last, counts)
                session.commit()
            except Exception:
                session.rollback()
                raise
            summary["pieces"] += 1
            summary["rows"] += len(rows)
            if progress:
                progress(i, len(runs), f"{underlying} {first} to {last}")
    log.info("Index minutes %s %s to %s: %s", underlying, start, end, summary)
    return summary


# Save the current option chain (every strike, CE and PE) with a timestamp.
def snapshot_option_chain(underlying: str, expiry: dt.date | None = None, client=None,
                          url: str | None = None) -> dict:
    """Saves contracts and option_quotes rows: LTP, bid/ask, OI, volume, IV, Greeks and spot.
    expiry: default = the nearest expiry."""
    underlying = underlying.upper()
    client = _client(client)
    expiry = expiry or client.get_expiry_list(underlying)[0]
    chain = client.get_option_chain(underlying, expiry)
    ts = dt.datetime.now(IST).replace(second=0, microsecond=0)
    with get_session(url) as session:
        underlying_id = _underlying_id(session, underlying)
        if not client.is_demo:
            purge_demo(session, underlying_id)
        contracts, quotes = [], []
        for row in chain.table.itertuples(index=False):
            for side in ("ce", "pe"):
                ltp = getattr(row, f"{side}_ltp")
                if ltp is None or pd.isna(ltp):
                    continue
                sid = getattr(row, f"{side}_security_id")
                contracts.append({"underlying_id": underlying_id, "expiry": expiry,
                                  "strike": float(row.strike), "option_type": side.upper(),
                                  "dhan_security_id": None if sid in (None, "DEMO") else sid,
                                  "is_demo": client.is_demo})
                quotes.append((float(row.strike), side.upper(), row, side))
        if contracts:
            upsert(session, OptionContract, contracts, ["underlying_id", "expiry", "strike", "option_type"])
        ids = {(float(s), t): cid for cid, s, t in session.execute(
            select(OptionContract.id, OptionContract.strike, OptionContract.option_type).where(
                OptionContract.underlying_id == underlying_id, OptionContract.expiry == expiry))}

        # Turn a possibly-empty number into a plain number or None.
        def num(value):
            return None if value is None or pd.isna(value) else float(value)

        rows = [{
            "contract_id": ids[(strike, opt)], "ts": ts, "ltp": num(getattr(r, f"{s}_ltp")),
            "bid": num(getattr(r, f"{s}_bid")), "ask": num(getattr(r, f"{s}_ask")),
            "volume": None if num(getattr(r, f"{s}_volume")) is None else int(getattr(r, f"{s}_volume")),
            "oi": None if num(getattr(r, f"{s}_oi")) is None else int(getattr(r, f"{s}_oi")),
            "iv": num(getattr(r, f"{s}_iv")), "delta": num(getattr(r, f"{s}_delta")),
            "gamma": num(getattr(r, f"{s}_gamma")), "theta": num(getattr(r, f"{s}_theta")),
            "vega": num(getattr(r, f"{s}_vega")), "underlying_price": float(chain.spot),
        } for strike, opt, r, s in quotes]
        if rows:
            upsert(session, OptionQuote, rows, ["contract_id", "ts"])
        session.commit()
    result = {"source": client.source, "underlying": underlying, "expiry": expiry, "ts": ts,
              "spot": chain.spot, "rows": len(rows)}
    log.info("Option chain snapshot: %s", result)
    return result


# Expiries that have stored minute bars on a given day, nearest first.
def stored_expiries(underlying: str, day: dt.date, url: str | None = None) -> list[dt.date]:
    start, stop = _day_bounds(day)
    with get_session(url) as session:
        rows = session.scalars(
            select(OptionContract.expiry).distinct()
            .join(IntradayOptionPrice, IntradayOptionPrice.contract_id == OptionContract.id)
            .join(Instrument, Instrument.id == OptionContract.underlying_id)
            .where(Instrument.symbol == underlying.upper(), IntradayOptionPrice.ts >= start,
                   IntradayOptionPrice.ts < stop))
        return sorted(rows)


# One day of one expiry, each column as one array, in time / strike / CE-PE order.
_ORDER = "ORDER BY p.ts, c.strike, c.option_type"
DAY_BARS_SQL = text(f"""
    SELECT array_agg(extract(epoch FROM p.ts)::float8 {_ORDER}), array_agg(c.strike::float8 {_ORDER}),
           array_agg(c.option_type {_ORDER}), array_agg(p.open::float8 {_ORDER}),
           array_agg(p.high::float8 {_ORDER}), array_agg(p.low::float8 {_ORDER}),
           array_agg(p.close::float8 {_ORDER}), array_agg(p.volume {_ORDER}), array_agg(p.oi {_ORDER}),
           array_agg(p.iv::float8 {_ORDER}), bool_or(c.is_demo)
    FROM intraday_option_prices p JOIN option_contracts c ON c.id = p.contract_id
    WHERE c.underlying_id = :uid AND c.expiry = :expiry AND p.ts >= :start AND p.ts < :stop""")
# The index's one-minute closes of that day (stored by download_index_minutes).
DAY_SPOT_SQL = text("""
    SELECT array_agg(extract(epoch FROM ts)::float8 ORDER BY ts), array_agg(close::float8 ORDER BY ts)
    FROM price_bars WHERE instrument_id = :uid AND timeframe = 1 AND ts >= :start AND ts < :stop""")


# One day's minute data for every stored strike of one expiry, as one table.
def load_option_day(underlying: str, day: dt.date, expiry: dt.date | None = None,
                    url: str | None = None) -> pd.DataFrame:
    """Columns: ts, strike, option_type, open, high, low, close, volume, oi, iv, spot, is_demo.
    expiry: default = the nearest expiry stored for that day. Used by the backtester and simulator.
    spot comes from the index minutes (download_index_minutes); empty if not downloaded."""
    underlying = underlying.upper()
    columns = ["ts", "strike", "option_type", "open", "high", "low", "close", "volume", "oi", "iv",
               "spot", "is_demo"]
    if expiry is None:
        found = stored_expiries(underlying, day, url)
        if not found:
            return pd.DataFrame(columns=columns)
        expiry = found[0]
    start, stop = _day_bounds(day)
    with get_session(url) as session:
        underlying_id = session.scalar(select(Instrument.id).where(Instrument.symbol == underlying))
        if underlying_id is None:
            return pd.DataFrame(columns=columns)
        # Speed: PostgreSQL sends each column as ONE array (times as epoch seconds, numbers as floats),
        # so Python never builds 15,000 row objects. A year of days loads several times faster.
        params = {"uid": underlying_id, "expiry": expiry, "start": start, "stop": stop}
        bars = session.execute(DAY_BARS_SQL, params).one()
        spots = session.execute(DAY_SPOT_SQL, params).one()
    if not bars[0]:
        return pd.DataFrame(columns=columns)
    epochs = np.array(bars[0], dtype=float)
    df = pd.DataFrame({"ts": pd.to_datetime(epochs, unit="s", utc=True).tz_convert(IST),
                       "strike": np.array(bars[1], dtype=float), "option_type": bars[2]})
    for i, col in enumerate(("open", "high", "low", "close"), start=3):
        df[col] = np.array(bars[i], dtype=float)
    for i, col in ((7, "volume"), (8, "oi")):
        values = bars[i]
        df[col] = pd.array(values, dtype="Int64") if None in values else np.array(values, dtype=np.int64)
    df["iv"] = np.array(bars[9], dtype=float)
    if spots[0]:
        where = pd.Index(np.array(spots[0], dtype=float)).get_indexer(epochs)
        spot = np.array(spots[1], dtype=float)[np.clip(where, 0, None)]
        df["spot"] = np.where(where >= 0, spot, np.nan)
    else:
        df["spot"] = np.nan
    df["is_demo"] = bool(bars[10])
    df.attrs.update({"underlying": underlying, "day": day, "expiry": expiry, "is_demo": bool(bars[10])})
    return df[columns]


# Days between start and end that have stored option minute bars.
def stored_days(underlying: str, start: dt.date, end: dt.date, url: str | None = None) -> list[dt.date]:
    first, _ = _day_bounds(start)
    _, last = _day_bounds(end)
    with get_session(url) as session:
        times = session.scalars(
            select(func.date_trunc("day", IntradayOptionPrice.ts)).distinct()
            .join(OptionContract, OptionContract.id == IntradayOptionPrice.contract_id)
            .join(Instrument, Instrument.id == OptionContract.underlying_id)
            .where(Instrument.symbol == underlying.upper(), IntradayOptionPrice.ts >= first,
                   IntradayOptionPrice.ts < last))
        return sorted({t.astimezone(IST).date() for t in times})


# Every stored day between start and end (nearest expiry each day), as one table.
def load_option_range(underlying: str, start: dt.date, end: dt.date,
                      url: str | None = None) -> pd.DataFrame:
    """Same columns as load_option_day, plus 'expiry'. Used for exports."""
    days = [load_option_day(underlying, d, url=url) for d in stored_days(underlying, start, end, url)]
    days = [d.assign(expiry=d.attrs["expiry"]) for d in days if not d.empty]
    return pd.concat(days, ignore_index=True) if days else pd.DataFrame()


# Turn 1-minute option bars into 5/15/25/60-minute bars (per strike and CE/PE).
def resample_option_bars(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Candles start at 09:15 like the exchange's: open = first, high = max, low = min,
    close = last, volume = sum; OI, IV and spot = their last value in the candle."""
    if minutes == 1 or df.empty:
        return df
    df = df.copy()
    session_start = df["ts"].dt.normalize() + pd.Timedelta(hours=9, minutes=15)
    offset = ((df["ts"] - session_start).dt.total_seconds() // 60 // minutes) * minutes
    df["ts"] = session_start + pd.to_timedelta(offset, unit="min")
    keys = ["ts", "strike", "option_type"] + (["expiry"] if "expiry" in df.columns else [])
    rules = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
             "oi": "last", "iv": "last", "spot": "last", "is_demo": "max"}
    rules = {k: v for k, v in rules.items() if k in df.columns}
    out = df.sort_values("ts").groupby(keys, as_index=False, sort=True).agg(rules)
    return out[[c for c in df.columns if c in out.columns]].sort_values(["ts", "strike", "option_type"]) \
        .reset_index(drop=True)
