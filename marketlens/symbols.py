"""Type-ahead search over every symbol the app knows: Nifty 50 / Nifty 200 stocks, ETFs and indices.

    search_symbols("relia") -> [{"symbol": "RELIANCE", "yahoo": "RELIANCE.NS", "name": "Reliance Industries Ltd.",
                                 "kind": "stock", "lists": ["nifty50", "nifty200"]}, ...]

Stocks and ETFs come from data/universe/*.csv; the indices are the ones the app's pages use (index cards,
sector heatmap). Best matches first: exact symbol, then symbol or name starts with the text, then a word of the
name starts with it, then the text appears anywhere (indices before stocks before ETFs when equal). At most 20 answers.
"""
import csv
import functools
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_DIR = ROOT / "data" / "universe"
# file name -> kind of instrument
LISTS = {"nifty50": "stock", "nifty200": "stock", "etfs": "etf"}
MAX_MATCHES = 20
KIND_ORDER = {"index": 0, "stock": 1, "etf": 2}      # equally good matches: indices first, then stocks, then ETFs


# The indices used by the app: (Yahoo ticker, name). Read from the pages' own lists so they never differ.
def index_symbols() -> list[tuple[str, str]]:
    from marketlens.overview import INDEX_CARDS, SECTOR_INDICES
    found = {ticker: name for name, _, ticker in INDEX_CARDS}
    for sector, ticker in SECTOR_INDICES:
        found.setdefault(ticker, f"Nifty {sector}")
    return list(found.items())


# Every known symbol once (a stock in both Nifty 50 and Nifty 200 lists both lists). Read once, then remembered.
@functools.lru_cache(maxsize=1)
def all_symbols(folder: Path = UNIVERSE_DIR) -> tuple[dict, ...]:
    rows: dict[str, dict] = {}
    for list_name, kind in LISTS.items():
        path = Path(folder) / f"{list_name}.csv"
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                symbol = (row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                item = rows.setdefault(symbol, {"symbol": symbol, "yahoo": f"{symbol}.NS",
                                                "name": (row.get("name") or symbol).strip(), "kind": kind, "lists": []})
                item["lists"].append(list_name)
    for ticker, name in index_symbols():
        rows.setdefault(ticker, {"symbol": ticker, "yahoo": ticker, "name": name, "kind": "index", "lists": []})
    return tuple(rows.values())


# How well one row matches the typed text: 0 = best ... 3 = weakest, None = no match.
def _rank(row: dict, text: str) -> int | None:
    symbol, name = row["symbol"].upper(), row["name"].upper()
    plain = symbol.lstrip("^")
    if text in (symbol, plain, row["yahoo"].upper()):
        return 0
    if symbol.startswith(text) or plain.startswith(text) or name.startswith(text):
        return 1
    if any(word.startswith(text) for word in re.split(r"[^A-Z0-9&]+", name) if word):
        return 2
    if text in symbol or text in name:
        return 3
    return None


# The best matches for what was typed (at most `limit`, never more than MAX_MATCHES).
def search_symbols(text: str, limit: int = MAX_MATCHES, rows=None) -> list[dict]:
    text = (text or "").strip().upper()
    if not text:
        return []
    rows = all_symbols() if rows is None else rows
    # indices keep the app's own order (Nifty 50 before the sector indices); the rest go A to Z
    ranked = [(rank, KIND_ORDER.get(row["kind"], 9), place if row["kind"] == "index" else 0, row["symbol"])
              for place, row in enumerate(rows) if (rank := _rank(row, text)) is not None]
    by_symbol = {row["symbol"]: row for row in rows}
    limit = max(1, min(limit, MAX_MATCHES))
    return [dict(by_symbol[s], lists=list(by_symbol[s]["lists"])) for *_, s in sorted(ranked)[:limit]]
