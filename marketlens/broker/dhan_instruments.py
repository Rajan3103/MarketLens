"""Security IDs and lot sizes: Dhan needs a Security ID for every request.

Dhan publishes one big public CSV (the "instrument master", about 2 lakh rows)
listing every instrument with its Security ID and lot size. We download it at
most once a day into data/dhan/scrip_master.csv and look things up in it.

    from marketlens.broker.dhan_instruments import INDICES, index_futures, find_stock, find_option
    INDICES["NIFTY"].security_id                 -> "13"
    index_futures("NIFTY")[0]                    -> current-month NIFTY futures contract
    find_stock("RELIANCE").security_id           -> "2885"
    find_option("NIFTY", expiry, 25000, "CE")    -> that option, with Security ID and lot size
    lot_size("NIFTY")                            -> 65
"""
import datetime as dt
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd
from sqlalchemy import select, update

from marketlens.logger import get_logger
from marketlens.models import Instrument
from marketlens.utils import retry

log = get_logger(__name__)

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
MASTER_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "dhan" / "scrip_master.csv"

# Short names for the columns we use from Dhan's file
COLUMNS = {
    "SEM_EXM_EXCH_ID": "exchange", "SEM_SEGMENT": "segment_code",
    "SEM_SMST_SECURITY_ID": "security_id", "SEM_INSTRUMENT_NAME": "instrument",
    "SEM_TRADING_SYMBOL": "symbol", "SEM_LOT_UNITS": "lot_size", "SEM_CUSTOM_SYMBOL": "display_name",
    "SEM_EXPIRY_DATE": "expiry", "SEM_SERIES": "series", "SM_SYMBOL_NAME": "name",
    "SEM_STRIKE_PRICE": "strike", "SEM_OPTION_TYPE": "option_type",
}
# Which index futures and options trade on which exchange
FUTURES_EXCHANGE = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "FINNIFTY": "NSE", "SENSEX": "BSE"}
OPTION_TYPES = ("CE", "PE")
MONTH_LABELS = ["Current", "Next", "Far"]
# Normal share series on NSE (others are bonds, rights, etc.)
STOCK_SERIES = ("EQ", "BE")


@dataclass(frozen=True)
class Security:
    """Everything Dhan needs to download one instrument, plus a few details to show."""
    symbol: str          # NIFTY, RELIANCE, NIFTY-Nov2026-FUT
    security_id: str     # Dhan's id, e.g. "13"
    segment: str         # IDX_I, NSE_EQ, NSE_FNO or BSE_FNO
    instrument: str      # INDEX, EQUITY, FUTIDX, OPTIDX or OPTSTK
    exchange: str        # NSE or BSE
    kind: str            # index, stock, future or option
    name: str = ""
    expiry: dt.date | None = None
    lot_size: int = 1
    label: str = ""      # e.g. "Current month" for futures
    strike: float | None = None        # options only
    option_type: str | None = None     # options only: CE (call) or PE (put)
    underlying: str = ""               # futures/options: NIFTY, RELIANCE ...

    # Text shown in the page's dropdowns.
    def describe(self) -> str:
        extra = f", expiry {self.expiry:%d-%b-%Y}, lot {self.lot_size}" if self.expiry else ""
        return f"{self.symbol} (Security ID {self.security_id}, {self.segment}{extra})"


# The four indices: fixed Security IDs, so they work even without the master file.
INDICES = {
    "NIFTY": Security("NIFTY", "13", "IDX_I", "INDEX", "NSE", "index", "Nifty 50"),
    "BANKNIFTY": Security("BANKNIFTY", "25", "IDX_I", "INDEX", "NSE", "index", "Nifty Bank"),
    "FINNIFTY": Security("FINNIFTY", "27", "IDX_I", "INDEX", "NSE", "index", "Nifty Financial Services"),
    "SENSEX": Security("SENSEX", "51", "IDX_I", "INDEX", "BSE", "index", "BSE Sensex"),
}


# Download Dhan's instrument list (tried again on network errors).
@retry
def download_master(path: Path = MASTER_FILE) -> Path:
    """Saves the CSV next to the project in data/dhan/ and returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with urllib.request.urlopen(MASTER_URL, timeout=60) as response:
        tmp.write_bytes(response.read())
    tmp.replace(path)  # only replace the old file once the download is complete
    log.info("Downloaded Dhan scrip master to %s", path)
    return path


# Keep only our columns, with short names and proper types.
def prepare_master(raw: pd.DataFrame) -> pd.DataFrame:
    """Turns Dhan's columns into: exchange, segment_code, security_id, instrument, symbol,
    lot_size, display_name, expiry (date), series, name."""
    df = raw.rename(columns=COLUMNS)[list(COLUMNS.values())].copy()
    for col in ("exchange", "segment_code", "security_id", "instrument", "symbol", "series", "name",
                "display_name", "option_type"):
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["lot_size"] = pd.to_numeric(df["lot_size"], errors="coerce").fillna(1).astype(int)
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    # Underlying = text before the first "-": NIFTY-Nov2026-25000-CE -> NIFTY
    df["underlying"] = df["symbol"].str.split("-").str[0]
    # Only the date part (2026-10-27 14:30:00 -> 2026-10-27) in one fixed format, so pandas
    # never guesses the format from an odd first row like 0001-01-01 (that becomes empty)
    df["expiry"] = pd.to_datetime(df["expiry"].fillna("").str[:10], format="%Y-%m-%d",
                                  errors="coerce").dt.date
    return df


# Read the cached master file (cached in memory until the file changes).
@lru_cache(maxsize=2)
def _read_master(path: str, modified: float) -> pd.DataFrame:
    return prepare_master(pd.read_csv(path, dtype=str, usecols=list(COLUMNS), low_memory=False))


# Return the instrument list, downloading a fresh copy once a day.
def load_master(path: Path = MASTER_FILE) -> pd.DataFrame:
    """Uses the saved copy if it is from today, or if the download fails (offline)."""
    fresh = path.exists() and dt.date.fromtimestamp(path.stat().st_mtime) == dt.date.today()
    if not fresh:
        try:
            download_master(path)
        except Exception as err:
            if not path.exists():
                raise RuntimeError(f"Could not download Dhan's instrument list: {err}") from err
            log.warning("Using the saved Dhan instrument list (download failed: %s)", err)
    return _read_master(str(path), path.stat().st_mtime)


# Live futures contracts of one index, nearest expiry first.
def index_futures(index: str, master: pd.DataFrame | None = None,
                  today: dt.date | None = None) -> list[Security]:
    """NIFTY, BANKNIFTY, FINNIFTY (NSE_FNO) or SENSEX (BSE_FNO); labelled Current / Next / Far."""
    index = index.upper()
    if index not in FUTURES_EXCHANGE:
        raise ValueError(f"Futures are available for {list(FUTURES_EXCHANGE)}, not {index!r}")
    master = load_master() if master is None else master
    today = today or dt.date.today()
    exchange = FUTURES_EXCHANGE[index]
    rows = master[(master["exchange"] == exchange) & (master["instrument"] == "FUTIDX")
                  & master["symbol"].str.startswith(f"{index}-")
                  & (master["expiry"] >= today)].sort_values("expiry")
    contracts = []
    for i, row in enumerate(rows.itertuples()):
        label = MONTH_LABELS[i] if i < len(MONTH_LABELS) else f"Month {i + 1}"
        contracts.append(Security(row.symbol, row.security_id, f"{exchange}_FNO", "FUTIDX", exchange,
                                  "future", row.display_name, row.expiry, row.lot_size,
                                  f"{label} month", underlying=index))
    return contracts


# Look up one NSE stock (or ETF) by its symbol.
def find_stock(symbol: str, master: pd.DataFrame | None = None) -> Security | None:
    """RELIANCE -> Security with ID 2885 (NSE_EQ). Returns None if not found."""
    master = load_master() if master is None else master
    rows = master[(master["exchange"] == "NSE") & (master["segment_code"] == "E")
                  & (master["symbol"] == symbol.strip().upper())]
    if rows.empty:
        return None
    row = rows.sort_values("series", key=lambda s: s != "EQ").iloc[0]  # prefer the EQ series
    return Security(row["symbol"], row["security_id"], "NSE_EQ", "EQUITY", "NSE", "stock",
                    row["name"] or row["display_name"])


# Find NSE stocks whose symbol or name contains the text (for the search box).
def search_stocks(text: str, master: pd.DataFrame | None = None, limit: int = 20) -> list[str]:
    """Returns matching symbols, exact matches first."""
    text = text.strip().upper()
    if not text:
        return []
    master = load_master() if master is None else master
    stocks = master[(master["exchange"] == "NSE") & (master["segment_code"] == "E")
                    & (master["instrument"] == "EQUITY") & master["series"].isin(STOCK_SERIES)]
    hits = stocks[stocks["symbol"].str.contains(text, regex=False)
                  | stocks["name"].str.upper().str.contains(text, regex=False)]
    symbols = sorted(set(hits["symbol"]), key=lambda s: (s != text, not s.startswith(text), s))
    return symbols[:limit]


# Rows of all live options for one underlying (index or stock).
def _options(underlying: str, master: pd.DataFrame) -> pd.DataFrame:
    """NIFTY/BANKNIFTY/FINNIFTY/SENSEX -> OPTIDX rows; stocks -> OPTSTK rows."""
    underlying = underlying.strip().upper()
    exchange = FUTURES_EXCHANGE.get(underlying, "NSE")
    kind = "OPTIDX" if underlying in FUTURES_EXCHANGE else "OPTSTK"
    return master[(master["exchange"] == exchange) & (master["instrument"] == kind)
                  & (master["underlying"] == underlying)]


# All live option expiry dates of one underlying, nearest first.
def option_expiries(underlying: str, master: pd.DataFrame | None = None,
                    today: dt.date | None = None) -> list[dt.date]:
    """NIFTY -> weekly Tuesdays (moved earlier before holidays); SENSEX -> weekly Thursdays."""
    master = load_master() if master is None else master
    today = today or dt.date.today()
    expiries = _options(underlying, master)["expiry"].dropna()
    return sorted(e for e in set(expiries) if e >= today)


# Find one option by underlying, expiry, strike and CE/PE.
def find_option(underlying: str, expiry: dt.date, strike: float, option_type: str,
                master: pd.DataFrame | None = None) -> Security | None:
    """find_option("NIFTY", date(2026, 11, 9), 25000, "CE") -> NIFTY 09 NOV 25000 CALL. None if not listed."""
    option_type = option_type.strip().upper()
    if option_type not in OPTION_TYPES:
        raise ValueError("option_type must be CE (call) or PE (put)")
    master = load_master() if master is None else master
    rows = _options(underlying, master)
    rows = rows[(rows["expiry"] == expiry) & (rows["option_type"] == option_type)
                & ((rows["strike"] - float(strike)).abs() < 0.01)]
    if rows.empty:
        return None
    row = rows.iloc[0]
    exchange = row["exchange"]
    return Security(row["symbol"], row["security_id"], f"{exchange}_FNO", row["instrument"], exchange,
                    "option", row["display_name"], row["expiry"], int(row["lot_size"]),
                    strike=float(row["strike"]), option_type=option_type,
                    underlying=underlying.strip().upper())


# All strikes listed for one underlying and expiry, lowest first.
def option_strikes(underlying: str, expiry: dt.date, master: pd.DataFrame | None = None) -> list[float]:
    """Used to build an option chain around the spot price."""
    master = load_master() if master is None else master
    rows = _options(underlying, master)
    return sorted(set(rows[rows["expiry"] == expiry]["strike"].dropna().astype(float)))


# Lot size of an index or stock's F&O contracts (from its nearest options).
def lot_size(underlying: str, master: pd.DataFrame | None = None) -> int | None:
    """lot_size("NIFTY") -> 65. None when the symbol has no F&O contracts."""
    master = load_master() if master is None else master
    rows = _options(underlying, master).sort_values("expiry")
    return int(rows["lot_size"].iloc[0]) if not rows.empty else None


# Turn a name into a Security: index name, futures/option symbol or stock symbol.
def resolve(symbol: str, master: pd.DataFrame | None = None) -> Security | None:
    """"NIFTY" -> index; "RELIANCE" -> stock; "NIFTY-Nov2026-FUT" -> future;
    "NIFTY-Nov2026-25000-CE" -> option. None if Dhan does not list it."""
    symbol = symbol.strip().upper()
    if symbol in INDICES:
        return INDICES[symbol]
    if "-" not in symbol:
        return find_stock(symbol, master)
    master = load_master() if master is None else master
    rows = master[master["symbol"].str.upper() == symbol]
    if rows.empty:
        return None
    row = rows.iloc[0]
    if row["instrument"].startswith("FUT"):
        return Security(row["symbol"], row["security_id"], f"{row['exchange']}_FNO", row["instrument"],
                        row["exchange"], "future", row["display_name"], row["expiry"],
                        int(row["lot_size"]), underlying=row["underlying"])
    if row["instrument"].startswith("OPT"):
        return find_option(row["underlying"], row["expiry"], row["strike"], row["option_type"], master)
    return None


# Save the Security ID in the instruments table; return the instrument's id.
def remember_security(session, sec: Security) -> int:
    """Adds the instrument if it is new; otherwise only fills in the Dhan details
    (so an existing ETF stays an ETF)."""
    if sec.kind not in ("index", "stock", "future"):
        raise ValueError("Options are stored in option_contracts, not instruments.")
    kind = {"index": "INDEX", "stock": "EQUITY", "future": "FUTURES"}[sec.kind]
    found = session.scalar(select(Instrument.id).where(Instrument.symbol == sec.symbol,
                                                       Instrument.exchange == sec.exchange))
    details = {"dhan_security_id": sec.security_id, "segment": sec.segment,
               "expiry": sec.expiry, "lot_size": sec.lot_size}
    if found:
        session.execute(update(Instrument).where(Instrument.id == found).values(**details))
        session.commit()
        return found
    inst = Instrument(symbol=sec.symbol, exchange=sec.exchange, instrument_type=kind,
                      name=sec.name or None, **details)
    session.add(inst)
    session.commit()
    return inst.id
