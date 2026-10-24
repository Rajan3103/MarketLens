"""Tests for marketlens/ingest/dhan_master.py using a tiny fake Dhan instrument list."""
import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import select

from marketlens.ingest.dhan_master import (INDICES, find_stock, index_futures, prepare_master,
                                           remember_security, search_stocks)
from marketlens.models import Instrument

TODAY = dt.date(2026, 10, 24)
HEADER = ["SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_SMST_SECURITY_ID", "SEM_INSTRUMENT_NAME",
          "SEM_EXPIRY_CODE", "SEM_TRADING_SYMBOL", "SEM_LOT_UNITS", "SEM_CUSTOM_SYMBOL",
          "SEM_EXPIRY_DATE", "SEM_STRIKE_PRICE", "SEM_OPTION_TYPE", "SEM_TICK_SIZE",
          "SEM_EXPIRY_FLAG", "SEM_EXCH_INSTRUMENT_TYPE", "SEM_SERIES", "SM_SYMBOL_NAME"]
# Rows copied in the same shape as Dhan's real file (24-Oct-2026)
ROWS = [
    ["NSE", "I", "13", "INDEX", "0", "NIFTY", "1.0", "Nifty 50", "0001-01-01", "", "XX", "0.05", "", "INDEX", "X", "NIFTY"],
    ["NSE", "D", "58875", "FUTIDX", "0", "NIFTY-Dec2026-FUT", "65.0", "NIFTY DEC FUT", "2026-12-29 14:30:00", "", "XX", "10", "M", "FUT", "", ""],
    ["NSE", "D", "48704", "FUTIDX", "0", "NIFTY-Oct2026-FUT", "65.0", "NIFTY OCT FUT", "2026-10-27 14:30:00", "", "XX", "10", "M", "FUT", "", ""],
    ["NSE", "D", "61471", "FUTIDX", "0", "NIFTY-Nov2026-FUT", "65.0", "NIFTY NOV FUT", "2026-11-23 14:30:00", "", "XX", "10", "M", "FUT", "", ""],
    ["NSE", "D", "40000", "FUTIDX", "0", "NIFTY-Sep2026-FUT", "75.0", "NIFTY SEP FUT", "2026-09-29 14:30:00", "", "XX", "10", "M", "FUT", "", ""],
    ["NSE", "D", "70000", "FUTIDX", "0", "NIFTYNXT50-Nov2026-FUT", "25.0", "NIFTYNXT50 NOV FUT", "2026-11-23 14:30:00", "", "XX", "10", "M", "FUT", "", ""],
    ["BSE", "D", "1100929", "FUTIDX", "0", "SENSEX-Nov2026-FUT", "20.0", "SENSEX NOV FUT", "2026-11-26 15:30:00", "", "", "5", "M", "FUTIDX", "", "BSXFUT"],
    ["BSE", "D", "1112362", "FUTIDX", "0", "SENSEX50-Nov2026-FUT", "75.0", "SENSEX50 NOV FUT", "2026-11-26 15:30:00", "", "", "5", "M", "FUTIDX", "", "SX50FUT"],
    ["NSE", "E", "2885", "EQUITY", "0", "RELIANCE", "1.0", "Reliance Industries", "", "", "", "10", "NA", "ES", "EQ", "RELIANCE INDUSTRIES LTD"],
    ["NSE", "E", "1333", "EQUITY", "0", "HDFCBANK", "1.0", "HDFC Bank", "", "", "", "10", "NA", "ES", "EQ", "HDFC BANK LTD"],
    ["NSE", "E", "99999", "EQUITY", "0", "HDFC2638DG", "1.0", "HDFC bond", "", "", "", "10", "NA", "ES", "N1", "HDFC BOND"],
]


# The fake instrument list in the same shape load_master() returns.
@pytest.fixture
def master():
    return prepare_master(pd.DataFrame(ROWS, columns=HEADER))


# The four indices have Dhan's fixed Security IDs.
def test_index_ids():
    assert {k: v.security_id for k, v in INDICES.items()} == {
        "NIFTY": "13", "BANKNIFTY": "25", "FINNIFTY": "27", "SENSEX": "51"}
    assert all(v.segment == "IDX_I" and v.instrument == "INDEX" for v in INDICES.values())


# NIFTY futures: only live contracts, nearest first, labelled Current/Next/Far.
def test_nifty_futures(master):
    futures = index_futures("NIFTY", master, today=TODAY)
    assert [f.symbol for f in futures] == ["NIFTY-Oct2026-FUT", "NIFTY-Nov2026-FUT", "NIFTY-Dec2026-FUT"]
    assert [f.label for f in futures] == ["Current month", "Next month", "Far month"]
    assert futures[0].security_id == "48704" and futures[0].segment == "NSE_FNO"
    assert futures[0].lot_size == 65 and futures[0].expiry == dt.date(2026, 10, 27)


# SENSEX futures come from BSE (BSE_FNO), and SENSEX50 is not mixed in.
def test_sensex_futures(master):
    futures = index_futures("SENSEX", master, today=TODAY)
    assert [f.symbol for f in futures] == ["SENSEX-Nov2026-FUT"]
    assert futures[0].segment == "BSE_FNO" and futures[0].exchange == "BSE"


# Unknown index for futures is refused.
def test_futures_unknown_index(master):
    with pytest.raises(ValueError):
        index_futures("MIDCAP", master, today=TODAY)


# Stock lookup gives the NSE_EQ Security ID, or None.
def test_find_stock(master):
    sec = find_stock("reliance", master)
    assert sec.security_id == "2885" and sec.segment == "NSE_EQ" and sec.instrument == "EQUITY"
    assert find_stock("NOSUCHCO", master) is None


# Search finds normal shares by symbol or name, not bonds.
def test_search_stocks(master):
    assert search_stocks("hdfc", master) == ["HDFCBANK"]
    assert search_stocks("industries", master) == ["RELIANCE"]
    assert search_stocks("  ", master) == []


# The Security ID is saved in instruments; an existing ETF keeps its type.
def test_remember_security(db_session, master):
    db_session.add(Instrument(symbol="RELIANCE", exchange="NSE", instrument_type="EQUITY"))
    db_session.commit()
    rid = remember_security(db_session, find_stock("RELIANCE", master))
    fid = remember_security(db_session, index_futures("NIFTY", master, today=TODAY)[0])
    rel = db_session.get(Instrument, rid)
    fut = db_session.get(Instrument, fid)
    assert rel.dhan_security_id == "2885" and rel.segment == "NSE_EQ"
    assert fut.instrument_type == "FUTURES" and fut.expiry == dt.date(2026, 10, 27)
    assert remember_security(db_session, find_stock("RELIANCE", master)) == rid  # no duplicate
    assert db_session.scalar(select(Instrument.id).where(Instrument.symbol == "NIFTY-Oct2026-FUT")) == fid
