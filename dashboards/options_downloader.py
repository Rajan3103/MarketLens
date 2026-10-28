"""Module 3 - Historical Data Downloader (options, stocks, ETFs and indices).

Tabs:
  1. Latest option chain     - live chain, save a snapshot, CSV / Excel
  2. Expired options         - minute bars for ATM-10..ATM+10 into PostgreSQL (feeds modules 4 and 5)
  3. Stocks, ETFs & indices  - free daily prices (yfinance) for one ticker, a list or a universe
  4. Intraday candles (Dhan) - index / futures / stock candles by Security ID, saved to a folder
Below the tabs: "What is stored" - every instrument with first date, last date and row count.
Without Dhan keys, tabs 1, 2 and 4 use clearly labelled DEMO data.
"""
import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketlens import config
from marketlens.broker.dhan_client import DhanError
from marketlens.broker.dhan_instruments import INDICES, find_stock, index_futures, search_stocks
from marketlens.broker.market_data import get_data_client
from marketlens.export import to_csv_bytes, to_excel_bytes
from marketlens.ingest.historical import (TIMEFRAMES, csv_name, download_candles, is_demo,
                                          save_csv, save_to_db)
from marketlens.ingest.options import (download_expired_options, load_option_day, load_option_range,
                                       resample_option_bars, snapshot_option_chain, stored_days,
                                       strike_labels)
from marketlens.ingest.prices import UNIVERSES, load_prices, load_universe, sync_prices
from marketlens.ingest.stored import what_is_stored
from marketlens.ui import key_notice

UNDERLYINGS = list(INDICES)                     # NIFTY, BANKNIFTY, FINNIFTY, SENSEX
INTERVALS = [1, 5, 15, 25, 60]
DEMO_BANNER = ("⚠️ DEMO DATA: the Dhan keys are not set, so prices are made up for practice "
               "(Black-Scholes from the real spot and India VIX). Not real prices.")

st.title("📥 Historical Data Downloader")
key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
           "Option and intraday data will be clearly labelled DEMO data until then.")


# ---------------- cached helpers (keep the page fast) ----------------

# Upcoming expiries of an underlying (asked again every 5 minutes at most).
@st.cache_data(ttl=300, show_spinner="Loading expiries ...")
def cached_expiries(underlying: str):
    return get_data_client().get_expiry_list(underlying)


# The option chain (Dhan allows one request every 3 seconds; we keep it 30 seconds).
@st.cache_data(ttl=30, show_spinner="Loading option chain ...")
def cached_chain(underlying: str, expiry: dt.date):
    return get_data_client().get_option_chain(underlying, expiry)


# What is stored in the database (refreshed every minute or with the button).
@st.cache_data(ttl=60, show_spinner="Reading the database ...")
def cached_stored():
    return what_is_stored()


# Dhan's instrument list, kept for an hour.
@st.cache_data(ttl=3600, show_spinner="Loading Dhan's instrument list ...")
def cached_futures(index: str):
    return index_futures(index)


@st.cache_data(ttl=3600, show_spinner="Looking up the Security ID ...")
def cached_stock(symbol: str):
    return find_stock(symbol)


@st.cache_data(ttl=3600)
def cached_search(text: str):
    return search_stocks(text)


# A copy without pandas "attrs" (extra notes such as the expiry), which Streamlit cannot display.
def plain(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.attrs = {}
    return out


# CSV and Excel buttons side by side for one table.
def export_buttons(df: pd.DataFrame, name: str, key: str, index: bool = False):
    c1, c2 = st.columns(2)
    df = plain(df)
    c1.download_button("⬇️ CSV", to_csv_bytes(df, index=index), file_name=f"{name}.csv",
                       mime="text/csv", key=f"{key}_csv")
    try:
        c2.download_button("⬇️ Excel", to_excel_bytes(df, index=index), file_name=f"{name}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key=f"{key}_xlsx")
    except ValueError as err:   # too many rows for Excel
        c2.caption(str(err))


# Run a long job with a progress bar; progress(done, total, text) updates it.
def with_progress(job):
    bar = st.progress(0.0, text="Starting ...")

    def progress(done, total, text=""):
        bar.progress(min(done / max(total, 1), 1.0), text=f"{done} of {total} · {text}")
    try:
        return job(progress)
    finally:
        bar.empty()


try:
    client = get_data_client()
except DhanError as err:           # DEMO_MODE=false and no keys
    client = None
    st.error(str(err))

chain_tab, expired_tab, stocks_tab, dhan_tab = st.tabs(
    ["Latest option chain", "Expired options", "Stocks, ETFs & indices", "Intraday candles (Dhan)"])

# =====================================================================
# Tab 1: latest option chain
# =====================================================================
with chain_tab:
    if client is None:
        st.info("The option chain needs Dhan keys or DEMO_MODE=true.")
    else:
        if client.is_demo:
            st.warning(DEMO_BANNER)
        c1, c2, c3 = st.columns([1, 1, 1])
        underlying = c1.selectbox("Underlying", UNDERLYINGS, key="ch_u")
        expiries = cached_expiries(underlying)
        expiry = c2.selectbox("Expiry", expiries, key="ch_exp", format_func=lambda d: f"{d:%a %d-%b-%Y}")
        width = c3.slider("Strikes each side of ATM", 5, 20, 10, key="ch_width")
        if st.button("🔄 Refresh chain", key="ch_refresh"):
            cached_chain.clear()
        chain = cached_chain(underlying, expiry)
        table = chain.table
        pcr = table["pe_oi"].sum() / max(table["ce_oi"].sum(), 1)
        days_left = (expiry - dt.date.today()).days
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Spot", f"{chain.spot:,.2f}")
        m2.metric("ATM strike", f"{chain.atm_strike:,.0f}")
        m3.metric("PCR (put OI / call OI)", f"{pcr:.2f}")
        m4.metric("Days to expiry", days_left)

        atm_pos = int((table["strike"] - chain.spot).abs().argmin())
        near = table.iloc[max(0, atm_pos - width): atm_pos + width + 1]
        view = near[["ce_oi", "ce_oi_change", "ce_iv", "ce_delta", "ce_ltp", "strike",
                     "pe_ltp", "pe_delta", "pe_iv", "pe_oi_change", "pe_oi"]]
        view.columns = ["CE OI", "CE OI chg", "CE IV", "CE delta", "CE LTP", "Strike",
                        "PE LTP", "PE delta", "PE IV", "PE OI chg", "PE OI"]
        st.dataframe(view.style.apply(
            lambda row: ["background-color: rgba(46,134,222,0.25)" if row["Strike"] == chain.atm_strike
                         else "" for _ in row], axis=1).format(precision=2),
            hide_index=True, width="stretch", height=min(40 + 35 * len(view), 760))

        s1, s2 = st.columns([1, 2])
        if s1.button("💾 Save snapshot to database", key="ch_save"):
            result = snapshot_option_chain(underlying, expiry)
            cached_stored.clear()
            s2.success(f"Saved {result['rows']} quotes at {result['ts']:%H:%M} ({result['source']}).")
        full = table.assign(underlying=underlying, expiry=expiry, spot=chain.spot, source=chain.source)
        export_buttons(full, f"{'DEMO_' if client.is_demo else ''}{underlying}_chain_{expiry}", "ch_exp")


# =====================================================================
# Tab 2: expired options (feeds the backtester and simulator)
# =====================================================================
with expired_tab:
    st.info("📚 This data feeds **4. Options Backtester** and **5. Option Simulator**: they replay these "
            "stored minute bars instead of calling Dhan again. The database always keeps **1-minute** "
            "bars; the interval below is only for the preview and the export.")
    if client is not None and client.is_demo:
        st.warning(DEMO_BANNER)
    c1, c2, c3 = st.columns(3)
    ex_u = c1.selectbox("Underlying", UNDERLYINGS, key="ex_u")
    yesterday = dt.date.today() - dt.timedelta(days=1)
    ex_from = c2.date_input("From date", yesterday - dt.timedelta(days=13), max_value=yesterday,
                            key="ex_from", format="DD/MM/YYYY")
    ex_to = c3.date_input("To date", yesterday, max_value=dt.date.today(), key="ex_to", format="DD/MM/YYYY")
    c4, c5, c6, c7 = st.columns(4)
    ex_flag = c4.radio("Expiry", ["weekly", "monthly"], horizontal=True, key="ex_flag",
                       help="Since 20-Nov-2024 only NIFTY and SENSEX have weekly options.")
    ex_width = c5.slider("Strikes around ATM (±)", 0, 10, 10, key="ex_width")
    ex_types = c6.multiselect("Option type", ["CE", "PE"], ["CE", "PE"], key="ex_types")
    ex_interval = c7.selectbox("Interval (preview / export)", INTERVALS, key="ex_int",
                               format_func=lambda m: f"{m} min")
    series = (2 * ex_width + 1) * len(ex_types)
    weekdays = len(pd.bdate_range(ex_from, ex_to)) if ex_to >= ex_from else 0
    st.caption(f"{series} series (ATM-{ex_width} .. ATM+{ex_width} × {', '.join(ex_types) or '-'}) × "
               f"{weekdays} weekdays ≈ {series * weekdays * 375:,} minute bars. "
               "Days already stored are skipped; you can stop and start again safely.")

    if st.button("⬇️ Download and store", type="primary", key="ex_go", disabled=not ex_types or client is None):
        try:
            summary = with_progress(lambda p: download_expired_options(
                ex_u, ex_from, ex_to, ex_flag, strike_labels(ex_width), tuple(ex_types),
                client=client, progress=p))
            st.session_state["ex_summary"] = summary
            cached_stored.clear()
        except (ValueError, DhanError) as err:
            st.error(str(err))
    if "ex_summary" in st.session_state:
        s = st.session_state["ex_summary"]
        st.success(f"{s['source']}: {s['rows']:,} minute bars saved in {s['pieces']} pieces; "
                   f"{s['skipped_days']} series-days were already stored.")

    st.subheader("Preview and export")
    days = stored_days(ex_u, ex_from, ex_to) if ex_to >= ex_from else []
    if not days:
        st.caption("Nothing stored for these dates yet. Click **Download and store** first.")
    else:
        day = st.selectbox("Day", days, index=len(days) - 1, key="ex_day",
                           format_func=lambda d: f"{d:%a %d-%b-%Y}")
        raw = load_option_day(ex_u, day)
        df = plain(resample_option_bars(raw, ex_interval))
        expiry = raw.attrs.get("expiry")
        demo = bool(df["is_demo"].any()) if not df.empty else False
        if demo:
            st.warning("These stored bars are DEMO data.")
        m1, m2, m3 = st.columns(3)
        m1.metric("Expiry", f"{expiry:%d-%b-%Y}" if expiry else "-")
        m2.metric("Strikes", df["strike"].nunique())
        m3.metric(f"{ex_interval}-min bars", f"{len(df):,}")
        if df["spot"].notna().any():
            first_spot = df["spot"].dropna().iloc[0]
            atm = df["strike"].iloc[(df["strike"] - first_spot).abs().argmin()]
            fig = go.Figure()
            for opt in sorted(set(df["option_type"])):
                leg = df[(df["strike"] == atm) & (df["option_type"] == opt)]
                fig.add_trace(go.Scatter(x=leg["ts"], y=leg["close"], name=f"{atm:,.0f} {opt}"))
            spot = df.drop_duplicates("ts")
            fig.add_trace(go.Scatter(x=spot["ts"], y=spot["spot"], name="Spot", yaxis="y2",
                                     line=dict(dash="dot", color="gray")))
            fig.update_layout(height=380, yaxis_title="Option price (Rs)",
                              yaxis2=dict(title="Spot", overlaying="y", side="right"),
                              legend=dict(orientation="h"),
                              title=f"Opening ATM strike {atm:,.0f}" + (" · DEMO" if demo else ""))
            st.plotly_chart(fig, width="stretch")
        st.dataframe(plain(df.head(500)), hide_index=True, width="stretch")
        prefix = f"{'DEMO_' if demo else ''}{ex_u}_options_{ex_interval}min"
        st.write("**This day**")
        export_buttons(df, f"{prefix}_{day}", "ex_day")
        st.write(f"**Whole range** ({days[0]:%d-%b} to {days[-1]:%d-%b}, {len(days)} days)")
        if st.button("Prepare whole-range export", key="ex_all"):
            st.session_state["ex_all_df"] = resample_option_bars(load_option_range(ex_u, ex_from, ex_to),
                                                                 ex_interval)
        if "ex_all_df" in st.session_state:
            export_buttons(st.session_state["ex_all_df"], f"{prefix}_{ex_from}_to_{ex_to}", "ex_range")

# =====================================================================
# Tab 3: stocks, ETFs and indices (free daily prices from yfinance)
# =====================================================================
with stocks_tab:
    st.write("Free daily prices from Yahoo Finance, stored in the database (only missing days are "
             "downloaded) and ready for the Momentum and ETF engines (modules 8 and 9).")
    mode = st.radio("What to download", ["Single ticker", "List", "Whole universe"], horizontal=True,
                    key="st_mode")
    if mode == "Single ticker":
        symbols = [st.text_input("NSE symbol, ETF or index (^NSEI = Nifty 50)", "RELIANCE",
                                 key="st_one").strip().upper()]
    elif mode == "List":
        text = st.text_area("Symbols (comma or one per line)", "TCS, INFY, HDFCBANK\nNIFTYBEES\n^NSEI",
                            key="st_list")
        symbols = [s.strip().upper() for s in text.replace("\n", ",").split(",") if s.strip()]
    else:
        universe = st.selectbox("Universe", UNIVERSES, key="st_univ")
        symbols = list(load_universe(universe)["symbol"])
        st.caption(f"{len(symbols)} symbols in {universe}.")
    symbols = [s for s in symbols if s]
    c1, c2 = st.columns(2)
    st_from = c1.date_input("From", dt.date.today() - dt.timedelta(days=365 * 5), key="st_from",
                            format="DD/MM/YYYY")
    st_to = c2.date_input("To", dt.date.today(), key="st_to", format="DD/MM/YYYY")

    if st.button("⬇️ Download and store", type="primary", key="st_go", disabled=not symbols):
        etfs = set(load_universe("etfs")["symbol"])
        groups = {"INDEX": [s for s in symbols if s.startswith("^")],
                  "ETF": [s for s in symbols if s in etfs],
                  "EQUITY": [s for s in symbols if not s.startswith("^") and s not in etfs]}
        lines = []
        for kind, group in groups.items():
            if group:
                summary = with_progress(lambda p: sync_prices(group, st_from, st_to, instrument_type=kind,
                                                              progress=p))
                lines.append(f"{kind.title()}: {summary}")
        st.session_state["st_done"] = lines
        cached_stored.clear()
    for line in st.session_state.get("st_done", []):
        st.success(line)

    closes = load_prices(symbols, st_from, st_to) if symbols else pd.DataFrame()
    if closes.empty:
        st.caption("Nothing stored for these symbols yet. Click **Download and store**.")
    else:
        shown = list(closes.columns[:10])
        growth = closes[shown] / closes[shown].bfill().iloc[0] * 100
        fig = go.Figure([go.Scatter(x=growth.index, y=growth[c], name=c) for c in shown])
        fig.update_layout(height=380, yaxis_title="Growth of 100", legend=dict(orientation="h"),
                          title="Close prices, starting at 100" + (" (first 10 shown)" if len(closes.columns) > 10 else ""))
        st.plotly_chart(fig, width="stretch")
        st.caption(f"{len(closes):,} trading days × {len(closes.columns)} symbols (close prices).")
        st.dataframe(closes.tail(200).round(2), width="stretch")
        export_buttons(closes.round(4), f"close_prices_{st_from}_to_{st_to}", "st_exp", index=True)

# =====================================================================
# Tab 4: intraday candles from Dhan by Security ID (index, futures, stocks)
# =====================================================================
with dhan_tab:
    st.write("Index, index futures and stock candles from Dhan (1/5/15/25/60 min or daily), "
             "saved as CSV in a folder you choose and, for real data, in the database.")

    # Show the Security ID details of the chosen instrument.
    def show_security(sec):
        c1, c2, c3 = st.columns(3)
        c1.metric("Security ID", sec.security_id)
        c2.metric("Segment", sec.segment)
        c3.metric("Instrument", sec.instrument)
        if sec.expiry:
            st.caption(f"Expiry {sec.expiry:%d-%b-%Y} · lot size {sec.lot_size} · futures history "
                       "starts when this contract was listed (about 3 months before expiry).")

    # Dates, timeframe, folder and the Download button.
    def download_form(sec, key: str):
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
                            help="Only real Dhan data is saved here; DEMO data never is.")
        st.caption("Intraday: last 5 years only, fetched in 90-day pieces. Daily: from listing.")
        if not st.button("Download", type="primary", key=f"{key}_go"):
            return
        try:
            df = with_progress(lambda p: download_candles(
                sec, start, end, timeframe, progress=lambda d, t: p(d, t, "pieces")))
        except Exception as err:
            st.error(str(err))
            return
        if df.empty:
            st.warning("No candles in this range (holidays or before the contract was listed).")
            return
        demo = is_demo(df)
        if demo:
            st.warning(DEMO_BANNER)
        path = save_csv(df, folder, csv_name(sec, timeframe, start, end, demo))
        st.success(f"Saved {len(df):,} candles to {path}")
        if to_db and not demo:
            st.info(f"Saved {save_to_db(df, sec, timeframe):,} candles to the database (price_bars).")
            cached_stored.clear()
        export_buttons(df, path.stem, f"{key}_exp")
        fig = go.Figure(go.Candlestick(x=df["timestamp"], open=df["open"], high=df["high"],
                                       low=df["low"], close=df["close"], name=sec.symbol))
        fig.update_layout(height=420, xaxis_rangeslider_visible=False,
                          title=f"{sec.symbol} · {timeframe}" + (" · DEMO" if demo else ""))
        st.plotly_chart(fig, width="stretch")

    kind = st.radio("Instrument", ["Index", "Index futures", "Stock"], horizontal=True, key="dh_kind")
    if kind == "Index":
        name = st.selectbox("Index", UNDERLYINGS, key="idx_name",
                            format_func=lambda n: f"{n} - {INDICES[n].name}")
        download_form(INDICES[name], "idx")
    elif kind == "Index futures":
        index = st.selectbox("Futures of", UNDERLYINGS, key="fut_index")
        try:
            contracts = cached_futures(index)
        except Exception as err:
            st.error(f"Could not load Dhan's instrument list: {err}")
            contracts = []
        if contracts:
            chosen = st.selectbox("Contract", contracts, key="fut_contract",
                                  format_func=lambda c: f"{c.label}: {c.symbol}")
            download_form(chosen, "fut")
    else:
        text = st.text_input("Search stock (symbol or company name)", value="RELIANCE", key="stk_search")
        try:
            matches = cached_search(text) if text.strip() else []
        except Exception as err:
            st.error(f"Could not load Dhan's instrument list: {err}")
            matches = []
        if matches:
            sec = cached_stock(st.selectbox("Stock", matches, key="stk_symbol"))
            if sec:
                download_form(sec, "stk")
        elif text.strip():
            st.warning(f"No NSE stock matches '{text}'.")

# =====================================================================
# What is stored
# =====================================================================
st.divider()
st.subheader("🗄️ What is stored")
c1, c2 = st.columns([3, 1])
if c2.button("🔄 Refresh", key="stored_refresh"):
    cached_stored.clear()
stored = cached_stored()
if stored.empty:
    st.caption("The database is empty so far.")
else:
    kinds = c1.multiselect("Show", sorted(stored["data"].unique()), sorted(stored["data"].unique()),
                           key="stored_kinds")
    shown = stored[stored["data"].isin(kinds)].copy()
    shown["demo"] = shown["demo"].map({True: "DEMO", False: "real"})
    st.dataframe(shown.sort_values(["data", "instrument"]), hide_index=True, width="stretch",
                 column_config={"rows": st.column_config.NumberColumn(format="%d")})
    st.caption(f"{len(shown)} rows · {shown['rows'].sum():,} stored records in total.")
