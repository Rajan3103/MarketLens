"""Shapes of the API's answers, and helpers that turn pandas tables and Plotly charts into JSON.

    df_to_records(df)   -> [{"date": "2026-11-03", "close": 2501.5, "ma50": null}, ...]
    table_of(df)        -> Table(columns=[...], rows=[...])
    fig_to_json(fig)    -> {"data": [...], "layout": {...}}   (Plotly.js draws it exactly like Python does)

JSON has no dates, no NaN and no numpy numbers, so: dates become ISO text ("2026-11-03" or
"2026-11-03T09:15:00+05:30"), NaN / NaT / infinity become null, numpy numbers become plain numbers.
"""
import base64
import datetime as dt
import json
import math
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel


class MetricCard(BaseModel):
    """One number on a card, like Streamlit's st.metric: 'Nifty 50 · 25,120 · +0.4%'."""
    label: str
    value: str | float | int | None
    delta: str | float | None = None
    help: str | None = None


class Table(BaseModel):
    """A table: the column names in order, then one dict per row."""
    columns: list[str]
    rows: list[dict[str, Any]]


class Chart(BaseModel):
    """A Plotly figure as JSON ({"data": [...], "layout": {...}}) for Plotly.js in the browser."""
    title: str | None = None
    figure: dict[str, Any]


class ErrorAnswer(BaseModel):
    """Every error the API sends: one plain-English sentence, never a Python traceback."""
    error: str


class Health(BaseModel):
    """GET /api/health."""
    status: str
    version: str
    database: str          # ok or fail
    mode: str              # PAPER or LIVE
    demo: bool             # True = DEMO data is shown (no Dhan keys)


# One cell as a JSON-friendly value.
def to_json_value(value):
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, dt.datetime)):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float, Decimal)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if value is pd.NaT or (not isinstance(value, (str, list, dict, tuple)) and pd.isna(value)):
        return None
    return value


# A pandas table as a list of JSON rows; a date index (like yfinance's) becomes a "date" column.
def df_to_records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    if not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
        if "index" in df.columns:
            df = df.rename(columns={"index": "date"})
    return [{str(k): to_json_value(v) for k, v in row.items()} for row in df.to_dict("records")]


# A pandas table as a Table answer (columns in order + rows).
def table_of(df: pd.DataFrame) -> Table:
    rows = df_to_records(df)
    columns = list(rows[0]) if rows else [str(c) for c in df.columns]
    return Table(columns=columns, rows=rows)


# A Plotly figure as JSON the browser can draw with Plotly.js (same data, same layout).
# Numbers become plain lists (Plotly 6+ would otherwise pack them as base64 "typed arrays").
def fig_to_json(fig) -> dict:
    from plotly.utils import PlotlyJSONEncoder
    return json.loads(json.dumps(_plain(fig.to_plotly_json()), cls=PlotlyJSONEncoder))


# Numpy arrays inside a figure as plain lists (dates as ISO text, NaN as null).
def _plain(obj):
    if isinstance(obj, dict) and "bdata" in obj and "dtype" in obj:      # a packed array: unpack it
        values = np.frombuffer(base64.b64decode(obj["bdata"]), dtype=np.dtype(obj["dtype"]))
        if obj.get("shape"):
            values = values.reshape([int(n) for n in str(obj["shape"]).split(",")])
        return _plain(values)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (np.ndarray, pd.Series, pd.Index)):
        values = np.asarray(obj)
        if values.dtype.kind == "M":                                  # datetime64
            return [to_json_value(v) for v in pd.to_datetime(values)]
        return [_plain(v) for v in values.tolist()]
    return to_json_value(obj)
