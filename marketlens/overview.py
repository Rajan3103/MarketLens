"""Logic behind the Live Market Overview page (module 1).

    snap = get_snapshot()          # Dhan quotes when the keys are set, otherwise yfinance
    snap.indices                   # Nifty 50, Bank Nifty, Fin Nifty, Sensex, India VIX
    top_movers(snap.stocks)        # 5 biggest gainers and losers of the Nifty 50
    breadth(snap.stocks)           # advances, declines, unchanged, advance/decline ratio
    market_is_open()               # 09:15-15:30 IST on a weekday

Everything except the two fetch functions is plain maths on tables, so it is easy to test.
"""
import datetime as dt
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from marketlens.broker.candles import IST, MARKET_CLOSE, MARKET_OPEN
from marketlens.broker.dhan_data import DhanDataClient
from marketlens.broker.dhan_instruments import INDICES, Security, find_stock, load_master
from marketlens.ingest.prices import load_universe, yf_ticker
from marketlens.logger import get_logger
from marketlens.utils import retry

log = get_logger(__name__)

# The five index cards: (card name, our symbol, Yahoo Finance ticker)
INDEX_CARDS = [
    ("Nifty 50", "NIFTY", "^NSEI"),
    ("Bank Nifty", "BANKNIFTY", "^NSEBANK"),
    ("Fin Nifty", "FINNIFTY", "NIFTY_FIN_SERVICE.NS"),
    ("Sensex", "SENSEX", "^BSESN"),
    ("India VIX", "INDIAVIX", "^INDIAVIX"),
]
# India VIX on Dhan (it has no options, so it is not in INDICES)
INDIA_VIX = Security("INDIAVIX", "21", "IDX_I", "INDEX", "NSE", "index", "India VIX")
SOURCE_DHAN = "Dhan (live)"
SOURCE_YF = "yfinance (about 15 minutes delayed)"
COLUMNS = ["symbol", "name", "last", "prev_close", "change", "pct_change"]


@dataclass
class Snapshot:
    """Everything the overview page shows, fetched in one go."""
    indices: pd.DataFrame      # one row per card, in INDEX_CARDS order
    stocks: pd.DataFrame       # one row per Nifty 50 stock
    source: str                # SOURCE_DHAN or SOURCE_YF
    as_of: dt.datetime         # when it was fetched (IST)


# ---------------- market hours ----------------

# True between 09:15 and 15:30 IST on Monday to Friday (exchange holidays are not known here).
def market_is_open(now: dt.datetime | None = None) -> bool:
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


# A short sentence about the market for the top of the page.
def market_status(now: dt.datetime | None = None) -> str:
    if market_is_open(now):
        return "Market open: refreshing every 60 seconds."
    return "Market closed, showing last close."


# ---------------- maths on tables (no internet) ----------------

# Add change and % change from last price and previous close.
def add_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Rows without both prices are dropped (e.g. a stock that did not trade)."""
    df = df.dropna(subset=["last", "prev_close"]).copy()
    df = df[df["prev_close"] > 0]
    df["change"] = df["last"] - df["prev_close"]
    df["pct_change"] = df["change"] / df["prev_close"] * 100
    return df.reset_index(drop=True)


# The n biggest gainers and the n biggest losers (by % change).
def top_movers(stocks: pd.DataFrame, n: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Gainers only include stocks that went up; losers only stocks that went down."""
    ranked = stocks.sort_values("pct_change", ascending=False)
    gainers = ranked[ranked["pct_change"] > 0].head(n)
    losers = ranked[ranked["pct_change"] < 0].tail(n).iloc[::-1]
    return gainers.reset_index(drop=True), losers.reset_index(drop=True)


# How many stocks rose, fell or stayed flat, and the advance/decline ratio.
def breadth(stocks: pd.DataFrame, flat_pct: float = 0.0) -> dict:
    """flat_pct: moves this small (in %) count as unchanged. Ratio is None when nothing fell."""
    up = int((stocks["pct_change"] > flat_pct).sum())
    down = int((stocks["pct_change"] < -flat_pct).sum())
    flat = len(stocks) - up - down
    return {"advances": up, "declines": down, "unchanged": flat,
            "ad_ratio": round(up / down, 2) if down else None}


# Colour for a change: green up, red down, grey flat (used by the page's tables).
def change_colour(value: float) -> str:
    if value > 0:
        return "color: #16a34a"
    if value < 0:
        return "color: #dc2626"
    return "color: gray"


# ---------------- fetching ----------------

# Last two daily closes for many Yahoo tickers in one request (tried again on errors).
@retry
def _yf_closes(tickers: list[str]) -> pd.DataFrame:
    data = yf.download(tickers, period="5d", interval="1d", group_by="ticker", auto_adjust=False,
                       progress=False, threads=True)
    if data is None or data.empty:
        raise ConnectionError("yfinance returned no data")
    return data


# Last price and previous close from a yfinance download.
def closes_from_yf(data: pd.DataFrame, ticker: str) -> tuple[float | None, float | None]:
    """During market hours the last row is today's (delayed) price; otherwise the last close."""
    try:
        close = data[ticker]["Close"].dropna()
    except KeyError:
        return None, None
    if len(close) < 2:
        return None, None
    return float(close.iloc[-1]), float(close.iloc[-2])


# Build a snapshot from yfinance (no keys needed).
def snapshot_from_yfinance(stock_symbols: list[str], names: dict) -> Snapshot:
    tickers = [t for _, _, t in INDEX_CARDS] + [yf_ticker(s) for s in stock_symbols]
    data = _yf_closes(tickers)
    idx = [(sym, card, *closes_from_yf(data, t)) for card, sym, t in INDEX_CARDS]
    stk = [(s, names.get(s, s), *closes_from_yf(data, yf_ticker(s))) for s in stock_symbols]
    return Snapshot(_table(idx), add_changes(_table(stk)), SOURCE_YF, dt.datetime.now(IST))


# Build a snapshot from Dhan quotes (one request for all indices and stocks).
def snapshot_from_dhan(client: DhanDataClient, stock_symbols: list[str], names: dict) -> Snapshot:
    master = load_master()
    secs = [INDICES.get(sym, INDIA_VIX) for _, sym, _ in INDEX_CARDS]
    stocks = [sec for sec in (find_stock(s, master) for s in stock_symbols) if sec is not None]
    quotes = client.get_quote(secs + stocks).set_index("symbol")
    # Look up last price and previous close for one symbol in the quote table.
    def pick(symbol):
        if symbol not in quotes.index:
            return None, None
        row = quotes.loc[symbol]
        return row["ltp"], row["close"]
    idx = [(sym, card, *pick(sym)) for card, sym, _ in INDEX_CARDS]
    stk = [(sec.symbol, names.get(sec.symbol, sec.symbol), *pick(sec.symbol)) for sec in stocks]
    return Snapshot(_table(idx), add_changes(_table(stk)), SOURCE_DHAN, dt.datetime.now(IST))


# Rows of (symbol, name, last, previous close) -> table with change columns for the cards.
def _table(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["symbol", "name", "last", "prev_close"])
    df["change"] = df["last"] - df["prev_close"]
    df["pct_change"] = df["change"] / df["prev_close"] * 100
    return df[COLUMNS]


# The whole overview: Dhan when its keys are set, otherwise yfinance.
def get_snapshot(client: DhanDataClient | None = None) -> Snapshot:
    """If Dhan fails (e.g. expired token), falls back to yfinance and logs why."""
    universe = load_universe("nifty50")
    symbols, names = list(universe["symbol"]), dict(zip(universe["symbol"], universe["name"]))
    client = client or DhanDataClient()
    if client.is_ready():
        try:
            return snapshot_from_dhan(client, symbols, names)
        except Exception as err:
            log.warning("Dhan quotes failed (%s); using yfinance instead", err)
    return snapshot_from_yfinance(symbols, names)


# ---------------- sector heatmap ----------------

# Nifty sector indices on Yahoo Finance: (sector name, ticker)
SECTOR_INDICES = [
    ("Bank", "^NSEBANK"), ("IT", "^CNXIT"), ("Auto", "^CNXAUTO"), ("Pharma", "^CNXPHARMA"),
    ("FMCG", "^CNXFMCG"), ("Metal", "^CNXMETAL"), ("Realty", "^CNXREALTY"),
    ("Energy", "^CNXENERGY"), ("PSU Bank", "^CNXPSUBANK"), ("Media", "^CNXMEDIA"),
]
# How far back each heatmap period compares, in trading days
WINDOWS = {"1 day": 1, "1 week": 5, "1 month": 21}


# About three months of daily closes for the sector indices (tried again on errors).
@retry
def _sector_history() -> pd.DataFrame:
    data = yf.download([t for _, t in SECTOR_INDICES], period="3mo", interval="1d", group_by="ticker",
                       auto_adjust=False, progress=False, threads=True)
    if data is None or data.empty:
        raise ConnectionError("yfinance returned no sector data")
    return data


# % change of each sector over the chosen period; sectors without enough data are skipped and logged.
def sector_changes(data: pd.DataFrame, window: str) -> tuple[pd.DataFrame, list[str]]:
    """Returns (table with sector, ticker, last, pct_change; list of skipped sectors).
    '1 week' = last close vs the close 5 trading days earlier; '1 month' = 21 trading days."""
    back = WINDOWS[window]
    rows, skipped = [], []
    for sector, ticker in SECTOR_INDICES:
        try:
            close = data[ticker]["Close"].dropna()
        except KeyError:
            close = pd.Series(dtype=float)
        if len(close) <= back:
            skipped.append(sector)
            log.warning("Sector heatmap: skipping %s (%s) - only %d closes, need %d",
                        sector, ticker, len(close), back + 1)
            continue
        last, before = float(close.iloc[-1]), float(close.iloc[-1 - back])
        rows.append({"sector": sector, "ticker": ticker, "last": last,
                     "pct_change": (last / before - 1) * 100})
    return pd.DataFrame(rows, columns=["sector", "ticker", "last", "pct_change"]), skipped


# Fetch and compute the heatmap table for one period.
def get_sector_heatmap(window: str = "1 day") -> tuple[pd.DataFrame, list[str]]:
    return sector_changes(_sector_history(), window)
