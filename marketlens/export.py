"""Turn tables into files for the download buttons: CSV and Excel (.xlsx).

Excel cannot store times with a time zone, so Indian times are written without the
"+05:30" (they are still Indian time). Excel also stops at 1,048,576 rows.
"""
import io

import pandas as pd

EXCEL_MAX_ROWS = 1_048_575   # one row is the header


# Remove the time zone from date-time columns (and the index) so Excel accepts them.
def _excel_ready(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_localize(None)
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


# A table as CSV bytes (times keep their +05:30).
def to_csv_bytes(df: pd.DataFrame, index: bool = False) -> bytes:
    return df.to_csv(index=index).encode("utf-8")


# One or more tables as an Excel file; a dict gives one sheet per table.
def to_excel_bytes(tables, index: bool = False) -> bytes:
    """tables: a DataFrame, or {"Sheet name": DataFrame, ...}. Raises ValueError if too big for Excel."""
    if isinstance(tables, pd.DataFrame):
        tables = {"Data": tables}
    # Check sizes first: stopping half-way through writing would leave a broken file
    for name, df in tables.items():
        if len(df) > EXCEL_MAX_ROWS:
            raise ValueError(f"'{name}' has {len(df):,} rows; Excel holds at most "
                             f"{EXCEL_MAX_ROWS:,}. Use the CSV download instead.")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, df in tables.items():
            _excel_ready(df).to_excel(writer, sheet_name=str(name)[:31], index=index)
    return buffer.getvalue()
