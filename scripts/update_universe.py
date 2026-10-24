"""Refresh the Nifty 50 and Nifty 200 stock lists from the official index files.

Run it from the MarketLens folder (NSE changes the index members twice a year):
    python scripts/update_universe.py

For each list it tries niftyindices.com first, then NSE's archive (same file).
If both fail, the saved file in data/universe/ is kept and used as before.
The ETF list (data/universe/etfs.csv) is chosen by hand, so it is not refreshed here.
"""
import csv
import http.cookiejar
import io
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from marketlens.logger import get_logger  # noqa: E402

log = get_logger("update_universe")
UNIVERSE_DIR = ROOT / "data" / "universe"

# Where each official list lives, and the fewest rows a good download must have
SOURCES = {
    "nifty50": (["https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
                 "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"], 45),
    "nifty200": (["https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
                  "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"], 190),
}
# These sites refuse requests that do not look like a web browser
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
           "Accept": "text/csv,text/html,*/*"}


# Download one CSV file as text (keeps cookies, which these sites need).
def fetch_text(url: str) -> str:
    """Return the file's text, or raise an error if the download fails."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=20) as response:
        return response.read().decode("utf-8-sig")


# Turn the official CSV (Company Name, Industry, Symbol, ...) into our 3 columns.
def parse_index_csv(text: str) -> list[dict]:
    """Return rows like {"symbol": "RELIANCE", "name": "Reliance Industries Ltd.", "sector": "Oil Gas ..."}."""
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        if row.get("Symbol"):
            rows.append({"symbol": row["Symbol"], "name": row.get("Company Name", ""),
                         "sector": row.get("Industry", "")})
    return rows


# Save rows to data/universe/<name>.csv.
def save_rows(name: str, rows: list[dict]) -> Path:
    """Write the symbol, name, sector columns, sorted by symbol."""
    path = UNIVERSE_DIR / f"{name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "name", "sector"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r["symbol"]))
    return path


# Try each source for one list; keep the saved file if none works.
def refresh(name: str) -> bool:
    """Return True if the list was refreshed, False if the saved file was kept."""
    urls, min_rows = SOURCES[name]
    for url in urls:
        try:
            rows = parse_index_csv(fetch_text(url))
        except Exception as err:
            log.warning("%s: %s failed (%s)", name, url.split("/")[2], str(err).splitlines()[0])
            continue
        if len(rows) < min_rows:
            log.warning("%s: %s gave only %d rows, ignoring it", name, url.split("/")[2], len(rows))
            continue
        path = save_rows(name, rows)
        print(f"OK: {name} refreshed from {url.split('/')[2]} ({len(rows)} stocks) -> {path}")
        return True
    saved = UNIVERSE_DIR / f"{name}.csv"
    if saved.exists():
        print(f"KEPT: could not download {name}; using the saved {saved.name}")
    else:
        print(f"PROBLEM: could not download {name} and there is no saved file yet")
    return False


if __name__ == "__main__":
    for list_name in SOURCES:
        refresh(list_name)
