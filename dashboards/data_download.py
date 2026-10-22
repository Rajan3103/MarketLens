"""Data Download: get daily prices for any ticker and save them as CSV.

Requirements FR-01 and FR-05.
"""
from datetime import date, timedelta

import streamlit as st

from marketlens.data import get_history_range

st.title("⬇️ Data Download")
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
