"""Module 3 - Historical Data Downloader.

Tabs: Index, Index futures, Stocks (all from Dhan, by Security ID),
Options (expired, planned) and Free daily (yfinance).
For each Dhan download you choose dates, timeframe and a folder; the candles
are saved as CSV in that folder and (real data only) in PostgreSQL.
"""
import datetime as dt

import plotly.graph_objects as go
import streamlit as st

from marketlens import config
from marketlens.broker.dhan_client import has_keys
from marketlens.data import get_history_range
from marketlens.ingest.dhan_master import INDICES, find_stock, index_futures, search_stocks
from marketlens.ingest.historical import (TIMEFRAMES, csv_name, download_candles, is_demo,
                                          save_csv, save_to_db)
from marketlens.ui import coming_soon, key_notice

st.title("📥 Historical Data Downloader")
key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
           "Downloads will make clearly labelled DEMO candles until then (not saved to the database).")


# Dhan's instrument list, kept for an hour so the page stays fast.
@st.cache_data(ttl=3600, show_spinner="Loading Dhan's instrument list ...")
def cached_futures(index: str):
    return index_futures(index)


# Same list, for looking up one stock.
@st.cache_data(ttl=3600, show_spinner="Looking up the Security ID ...")
def cached_stock(symbol: str):
    return find_stock(symbol)


# Same list, for the stock search box.
@st.cache_data(ttl=3600)
def cached_search(text: str):
    return search_stocks(text)


# Show the Security ID details of the chosen instrument.
def show_security(sec):
    c1, c2, c3 = st.columns(3)
    c1.metric("Security ID", sec.security_id)
    c2.metric("Segment", sec.segment)
    c3.metric("Instrument", sec.instrument)
    if sec.expiry:
        st.caption(f"Expiry {sec.expiry:%d-%b-%Y} · lot size {sec.lot_size} · "
                   "futures history starts when this contract was listed (about 3 months before expiry).")


# Dates, timeframe, folder and the Download button, shared by every Dhan tab.
def download_form(sec, key: str):
    """key: a short unique name per tab, so the tabs keep separate inputs."""
    show_security(sec)
    today = dt.date.today()
    c1, c2, c3 = st.columns(3)
    start = c1.date_input("From date", value=today - dt.timedelta(days=30), max_value=today,
                          key=f"{key}_from", format="DD/MM/YYYY")
    end = c2.date_input("To date", value=today, max_value=today, key=f"{key}_to", format="DD/MM/YYYY")
    timeframe = c3.selectbox("Timeframe", list(TIMEFRAMES), index=1, key=f"{key}_tf")
    f1, f2 = st.columns([3, 1])
    folder = f1.text_input("Download folder", value=config.DOWNLOAD_DIR, key=f"{key}_dir",
                           help="A folder on this computer, e.g. C:/Users/you/Documents/MarketLens. "
                                "Relative folders are inside the MarketLens folder.")
    to_db = f2.checkbox("Also save to database", value=True, key=f"{key}_db",
                        help="Only real Dhan data is saved; DEMO data never is.")
    st.caption("Intraday: last 5 years only, fetched in 90-day pieces. Daily: from listing.")

    if not st.button("Download", type="primary", key=f"{key}_go"):
        return
    bar = st.progress(0.0, text="Downloading ...")
    try:
        df = download_candles(sec, start, end, timeframe,
                              progress=lambda done, total: bar.progress(
                                  done / total, text=f"Piece {done} of {total}"))
    except Exception as err:  # wrong dates, key problem, Dhan error ...
        bar.empty()
        st.error(str(err))
        return
    bar.empty()
    if df.empty:
        st.warning("No candles in this range (holidays or before the contract was listed).")
        return

    demo = is_demo(df)
    if demo:
        st.warning("⚠️ DEMO DATA: made up for practice because the Dhan keys are not set. "
                   "Not real prices.")
    path = save_csv(df, folder, csv_name(sec, timeframe, start, end, demo))
    st.success(f"Saved {len(df):,} candles to {path}")
    if to_db and not demo:
        st.info(f"Saved {save_to_db(df, sec, timeframe):,} candles to the database (price_bars).")
    st.download_button("Also download to this browser", df.to_csv(index=False).encode("utf-8"),
                       file_name=path.name, mime="text/csv", key=f"{key}_dl")

    fig = go.Figure(go.Candlestick(x=df["timestamp"], open=df["open"], high=df["high"],
                                   low=df["low"], close=df["close"], name=sec.symbol))
    fig.update_layout(height=420, xaxis_rangeslider_visible=False,
                      title=f"{sec.symbol} · {timeframe}" + (" · DEMO" if demo else ""))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(df.head(500), width="stretch", hide_index=True)


index_tab, futures_tab, stocks_tab, options_tab, free_tab = st.tabs(
    ["Index", "Index futures", "Stocks", "Options (expired)", "Free daily (yfinance)"])

# ---- Tab 1: indices (fixed Security IDs) ----
with index_tab:
    name = st.selectbox("Index", list(INDICES), key="idx_name",
                        format_func=lambda n: f"{n} - {INDICES[n].name}")
    download_form(INDICES[name], "idx")

# ---- Tab 2: index futures (live contracts from Dhan's list) ----
with futures_tab:
    index = st.selectbox("Futures of", ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"], key="fut_index")
    try:
        contracts = cached_futures(index)
    except Exception as err:
        st.error(f"Could not load Dhan's instrument list: {err}")
        contracts = []
    if contracts:
        chosen = st.selectbox("Contract", contracts, key="fut_contract",
                              format_func=lambda c: f"{c.label}: {c.symbol}")
        download_form(chosen, "fut")
    elif index:
        st.info("No live futures contracts found.")

# ---- Tab 3: stocks (search by symbol or name) ----
with stocks_tab:
    text = st.text_input("Search stock (symbol or company name)", value="RELIANCE", key="stk_search")
    try:
        matches = cached_search(text) if text.strip() else []
    except Exception as err:
        st.error(f"Could not load Dhan's instrument list: {err}")
        matches = []
    if matches:
        symbol = st.selectbox("Stock", matches, key="stk_symbol")
        sec = cached_stock(symbol)
        if sec:
            download_form(sec, "stk")
    elif text.strip():
        st.warning(f"No NSE stock matches '{text}'.")

# ---- Tab 4: expired options (planned) ----
with options_tab:
    coming_soon("Expired options data", [
        "Expired NIFTY / BANKNIFTY / SENSEX options from Dhan: minute candles, OI and IV",
        "Pick strikes by ATM offset (ATM, ATM+1, ATM-1 ...), CE or PE, weekly or monthly expiry",
        "Saved in the database for the Options Backtester and Option Simulator",
    ])

# ---- Tab 5: free daily prices from yfinance (no key needed) ----
with free_tab:
    st.write("Free daily Open, High, Low, Close and Volume for any NSE stock, ETF or index "
             "(Yahoo Finance, adjusted for splits and dividends).")
    ticker = st.text_input("Ticker (NSE stocks end in .NS, Nifty 50 is ^NSEI)",
                           value="NIFTYBEES.NS", key="yf_ticker").strip().upper()
    c1, c2 = st.columns(2)
    start = c1.date_input("From", value=dt.date.today() - dt.timedelta(days=365 * 3), key="yf_from")
    end = c2.date_input("To", value=dt.date.today(), key="yf_to")
    if st.button("Get data", type="primary", key="yf_go"):
        df = get_history_range(ticker, start, end)
        if df.empty:
            st.error(f"No data found for {ticker}.")
        else:
            st.success(f"{len(df)} trading days downloaded for {ticker}.")
            st.dataframe(df.round(2), width="stretch")
            st.download_button("Save as CSV", df.to_csv().encode("utf-8"),
                               file_name=f"{ticker}_{start}_{end}.csv", mime="text/csv")

if not has_keys():
    st.caption("Tip: once DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN are in .env, restart the app "
               "and the same buttons download real Dhan candles.")
