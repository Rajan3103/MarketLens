"""Module 3 - Options Data Downloader.

Tab 1 (planned): expired and live options data from Dhan.
Tab 2 (ready): daily prices for any stock, ETF or index from yfinance, saved as CSV.
"""
from datetime import date, timedelta

import streamlit as st

from marketlens.data import get_history_range
from marketlens.ui import coming_soon, key_notice

st.title("📥 Options Data Downloader")
options_tab, equity_tab = st.tabs(["Options data (Dhan)", "Equity / ETF / index (yfinance)"])

# ---- Tab 1: options data (planned) ----
with options_tab:
    key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
               "Options downloads need Dhan; DEMO data will be offered until then.")
    coming_soon("Options Data Downloader", [
        "Expired options data from Dhan: minute candles, OI and IV by strike (ATM, ATM+1, ...)",
        "Pick index, expiry type (weekly / monthly), strikes and date range",
        "Save into the PostgreSQL database for the backtester and simulator",
        "Download as CSV",
    ])

# ---- Tab 2: equity / ETF / index daily prices (ready) ----
with equity_tab:
    st.write("Download daily Open, High, Low, Close and Volume for any NSE stock, ETF or index.")

    ticker = st.text_input("Ticker (NSE stocks end in .NS, Nifty 50 is ^NSEI)",
                           value="NIFTYBEES.NS").strip().upper()
    c1, c2 = st.columns(2)
    start = c1.date_input("From", value=date.today() - timedelta(days=365 * 3))
    end = c2.date_input("To", value=date.today())

    if st.button("Get data", type="primary"):
        df = get_history_range(ticker, start, end)
        if df.empty:
            st.error(f"No data found for {ticker}.")
        else:
            st.success(f"{len(df)} trading days downloaded for {ticker}.")
            st.dataframe(df.round(2), width="stretch")
            st.download_button("Save as CSV", df.to_csv().encode("utf-8"),
                               file_name=f"{ticker}_{start}_{end}.csv", mime="text/csv")
