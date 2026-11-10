"""Logic behind the Live Market Overview page (module 1).

    snap = get_snapshot()          # Dhan quotes when the keys are set, otherwise yfinance
    snap.indices                   # Nifty 50, Bank Nifty, Fin Nifty, Sensex, India VIX
    top_movers(snap.stocks)        # 5 biggest gainers and losers of the Nifty 50
    breadth(snap.stocks)           # advances, declines, unchanged, advance/decline ratio
    market_is_open()               # 09:15-15:30 IST on a weekday
    indicator_table(prices, 20, 50, months=12)   # MAs, crossovers, MA200, RSI(14), Bollinger Bands
    indicator_chart(table, 20, 50)               # price + RSI + volume panels (also used by the web API)

Everything except the two fetch functions is plain maths on tables, so it is easy to test.
"""
import datetime as dt
from dataclasses import dataclass

import pandas as pd
import plotly.graph_objects as go
import yfinance as yf
from plotly.subplots import make_subplots

from marketlens.broker.candles import IST, MARKET_CLOSE, MARKET_OPEN
from marketlens.broker.dhan_data import DhanDataClient
from marketlens.broker.dhan_instruments import INDICES, Security, find_stock, load_master
from marketlens.data import add_moving_averages
from marketlens.ingest.prices import load_universe, yf_ticker
from marketlens.logger import get_logger
from marketlens.signals.indicators import bollinger, rsi, sma
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


# Everything the top of the page shows, from one snapshot: cards, movers and breadth.
def summarise(snap: Snapshot, n: int = 5) -> dict:
    """indices / gainers / losers are tables; breadth is the dict from breadth()."""
    gainers, losers = top_movers(snap.stocks, n)
    return {"indices": snap.indices, "gainers": gainers, "losers": losers, "breadth": breadth(snap.stocks),
            "stock_count": len(snap.stocks), "source": snap.source, "live": snap.source == SOURCE_DHAN,
            "as_of": snap.as_of}


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
def sector_history() -> pd.DataFrame:
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
    return sector_changes(sector_history(), window)


# Treemap of the sector table: green up, red down; the size of the move sets the colour strength.
def sector_chart(table: pd.DataFrame, height: int = 360) -> go.Figure:
    biggest = max(table["pct_change"].abs().max(), 0.5) if len(table) else 0.5   # readable on quiet days
    fig = go.Figure(go.Treemap(
        labels=table["sector"], parents=[""] * len(table), values=[1] * len(table),
        customdata=table[["pct_change", "last"]].to_numpy(),
        texttemplate="<b>%{label}</b><br>%{customdata[0]:+.2f}%",
        hovertemplate="%{label}<br>Change: %{customdata[0]:+.2f}%<br>Index: %{customdata[1]:,.2f}<extra></extra>",
        marker=dict(colors=table["pct_change"], colorscale="RdYlGn", cmid=0, cmin=-biggest, cmax=biggest,
                    colorbar=dict(title="%")),
    ))
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=10, b=0))
    return fig


# One stacked bar: advances (green), unchanged (grey), declines (red).
def breadth_chart(b: dict) -> go.Figure:
    fig = go.Figure()
    for label, value, colour in (("Advances", b["advances"], "#16a34a"), ("Unchanged", b["unchanged"], "#9ca3af"),
                                 ("Declines", b["declines"], "#dc2626")):
        fig.add_trace(go.Bar(x=[value], y=["Nifty 50"], orientation="h", name=label, marker_color=colour,
                             text=[value], textposition="inside"))
    fig.update_layout(barmode="stack", height=120, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
                      xaxis=dict(visible=False))
    return fig


# ---------------- price chart with indicators ----------------

# Period shown -> (period downloaded, months kept). The extra history warms up the 200-day average and RSI.
CHART_PERIODS = {"6mo": ("2y", 6), "1y": ("2y", 12), "2y": ("5y", 24), "5y": ("10y", 60)}


# Add the fast / slow averages and crossovers, MA200, RSI(14) and Bollinger Bands (20, 2), then keep the last months.
def indicator_table(prices: pd.DataFrame, fast: int = 20, slow: int = 50, months: int | None = None) -> pd.DataFrame:
    """prices: Open/High/Low/Close/Volume with a date index. months=None keeps every row.
    Columns added: MA<fast>, MA<slow>, Signal (+1 bullish, -1 bearish), MA200, RSI, mid, upper, lower."""
    if fast >= slow:
        raise ValueError(f"The fast average ({fast} days) must be shorter than the slow one ({slow} days).")
    table = add_moving_averages(prices, fast, slow)
    table["MA200"] = sma(table["Close"], 200)
    table["RSI"] = rsi(table["Close"], 14)
    table = table.join(bollinger(table["Close"], 20, 2))
    if months and len(table):
        table = table[table.index >= table.index[-1] - pd.DateOffset(months=months)]
    return table


# True when every volume is 0 or missing (Yahoo has no volume for indices).
def has_no_volume(table: pd.DataFrame) -> bool:
    return bool(table["Volume"].fillna(0).eq(0).all())


# Three panels: candles with averages and crossover markers, RSI(14) with 70 / 30 lines, and volume.
def indicator_chart(df: pd.DataFrame, fast: int = 20, slow: int = 50, show_ma200: bool = False,
                    show_bb: bool = False, title: str = "", height: int = 800) -> go.Figure:
    """df: the output of indicator_table."""
    buys, sells = df[df["Signal"] == 1], df[df["Signal"] == -1]
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.6, 0.2, 0.2],
                        subplot_titles=("", "RSI (14)", "Volume"))
    fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
                                 name="Price"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df[f"MA{fast}"], name=f"{fast}-day MA", line=dict(color="#2a78d6")),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df[f"MA{slow}"], name=f"{slow}-day MA", line=dict(color="#eb6834")),
                  row=1, col=1)
    if show_ma200:
        fig.add_trace(go.Scatter(x=df.index, y=df["MA200"], name="200-day MA", line=dict(color="purple", width=2)),
                      row=1, col=1)
    if show_bb:
        fig.add_trace(go.Scatter(x=df.index, y=df["upper"], name="Bollinger upper",
                                 line=dict(color="gray", width=1, dash="dot")), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["lower"], name="Bollinger lower", fill="tonexty",
                                 fillcolor="rgba(128,128,128,0.12)", line=dict(color="gray", width=1, dash="dot")),
                      row=1, col=1)
    fig.add_trace(go.Scatter(x=buys.index, y=buys["Close"], mode="markers", name="Buy (bullish cross)",
                             marker=dict(symbol="triangle-up", size=12, color="#16a34a")), row=1, col=1)
    fig.add_trace(go.Scatter(x=sells.index, y=sells["Close"], mode="markers", name="Sell (bearish cross)",
                             marker=dict(symbol="triangle-down", size=12, color="#dc2626")), row=1, col=1)

    # RSI panel with the usual 70 / 30 lines
    fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], name="RSI 14", line=dict(color="#2e86de")), row=2, col=1)
    fig.add_hrect(y0=30, y1=70, fillcolor="rgba(46,134,222,0.08)", line_width=0, row=2, col=1)
    for level in (70, 30):
        fig.add_hline(y=level, line_dash="dash", line_color="gray", line_width=1, row=2, col=1)

    # Volume panel: green when the day closed up, red when it closed down
    colours = ["#16a34a" if c >= o else "#dc2626" for o, c in zip(df["Open"], df["Close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="Volume", marker_color=colours, showlegend=False),
                  row=3, col=1)

    fig.update_layout(title=title or None, height=height, xaxis_rangeslider_visible=False, hovermode="x unified",
                      legend=dict(orientation="h", y=1.04), margin=dict(l=10, r=10, t=60 if title else 30, b=10))
    fig.update_yaxes(title_text="Price (Rs)", row=1, col=1)
    fig.update_yaxes(range=[0, 100], title_text="RSI (0-100)", row=2, col=1)
    fig.update_yaxes(title_text="Volume (shares)", row=3, col=1)
    fig.update_xaxes(title_text="Date", row=3, col=1)
    return fig
