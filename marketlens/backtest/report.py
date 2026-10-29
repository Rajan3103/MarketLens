"""Backtest reports as files: an Excel workbook and a trades CSV.

    export_excel(result) -> bytes with sheets Summary, Trades, Daily P&L, Monthly P&L
"""
import pandas as pd

from marketlens.backtest.charts import monthly_table
from marketlens.backtest.engine import BacktestResult
from marketlens.backtest.metrics import METRIC_LABELS
from marketlens.export import to_csv_bytes, to_excel_bytes

NOTE = "Historical analysis for learning, not a live trading recommendation."


# The Summary sheet: the backtest details first, then every metric with its label.
def summary_table(result: BacktestResult) -> pd.DataFrame:
    rows = [("Backtest", result.name),
            ("Data", "DEMO (made-up) data" if result.is_demo else "Stored market data"),
            ("Period", f"{result.start:%d-%b-%Y} to {result.end:%d-%b-%Y}"),
            ("Starting capital (Rs)", result.capital),
            ("Benchmark", result.benchmark_name or "-")]
    rows += [(f"Setting: {k}", str(v)) for k, v in result.params.items()]
    rows += [(label, "-" if result.metrics.get(key) is None else round(result.metrics[key], 2))
             for key, label in METRIC_LABELS.items()]
    rows.append(("Note", NOTE))
    return pd.DataFrame(rows, columns=["Metric", "Value"])


# All four sheets in one Excel file (bytes, ready for st.download_button).
def export_excel(result: BacktestResult) -> bytes:
    return to_excel_bytes({"Summary": summary_table(result), "Trades": result.trades_frame(),
                           "Daily P&L": result.daily_frame(), "Monthly P&L": monthly_table(result)})


# The trade list as a CSV file (bytes).
def export_trades_csv(result: BacktestResult) -> bytes:
    return to_csv_bytes(result.trades_frame())


# A safe file name from the backtest name, e.g. "NIFTY_short_straddle_2025-01-01_2025-12-31".
def file_stem(result: BacktestResult) -> str:
    name = "".join(c if c.isalnum() else "_" for c in result.name).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    return f"{name}_{result.start}_{result.end}"
