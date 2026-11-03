"""Price alerts: "tell me on Telegram when ...". Saved in the price_alerts table, checked by check_alerts().

    add_alert("NIFTY", "ABOVE", 25_500)                       # price above / below a level
    add_alert("RELIANCE", "CHANGE_BELOW", -2)                 # % change today (from yesterday's close)
    add_alert("TCS", "INDICATOR", rule={"conditions": [{"left": {"kind": "rsi", "length": 14}, "op": "<",
                                                         "right": {"kind": "number", "value": 30}}]})
    add_alert("NIFTY", "PCR_ABOVE", 1.3)                      # put-call ratio of the nearest expiry
    add_alert("BANKNIFTY", "OI_ABOVE", 5_000_000, side="pe")  # total / call / put open interest
    check_alerts()          # checks every active alert; a triggered one sends Telegram and is switched off

Prices: Dhan quotes when the keys are set, otherwise yfinance (about 15 minutes delayed). Indicator alerts use
daily candles from yfinance and the same rules as the Signals builder (marketlens/signals/rules.py); they
trigger on the last finished day. OI and PCR come from the option chain of the nearest expiry and need Dhan:
on DEMO data they are checked but never triggered.

    python -m marketlens.alerts.price_alerts list | check | watch [--every 60] | delete ID
"""
import argparse
import datetime as dt
import time

import pandas as pd

from marketlens.broker.candles import IST
from marketlens.logger import get_logger, record_error

log = get_logger(__name__)

CONDITIONS = {
    "ABOVE": "price above", "BELOW": "price below",
    "CHANGE_ABOVE": "% change today above", "CHANGE_BELOW": "% change today below",
    "INDICATOR": "indicator rule true",
    "OI_ABOVE": "open interest above", "OI_BELOW": "open interest below",
    "PCR_ABOVE": "put-call ratio above", "PCR_BELOW": "put-call ratio below",
}
PRICE, CHANGE, CHAIN = ("ABOVE", "BELOW"), ("CHANGE_ABOVE", "CHANGE_BELOW"), ("OI_ABOVE", "OI_BELOW", "PCR_ABOVE",
                                                                             "PCR_BELOW")
OPTION_INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX")
OI_SIDES = {"total": "total", "ce": "call", "pe": "put"}
# Our index names -> Yahoo Finance tickers (stocks: RELIANCE -> RELIANCE.NS)
INDEX_TICKERS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS", "SENSEX": "^BSESN",
                 "INDIAVIX": "^INDIAVIX"}
WATCH_SECONDS = 60
HEARTBEAT = "price_alerts"


# ---------------------------------------------------------------- saving alerts

# Check an alert's settings and return them cleaned: (symbol, condition, target, params).
def clean_alert(symbol: str, condition: str, target: float | None = None, rule: dict | None = None,
                side: str = "total") -> tuple[str, str, float | None, dict]:
    from marketlens.signals.rules import JOINS, Condition
    symbol, condition = (symbol or "").strip().upper(), (condition or "").strip().upper()
    if not symbol:
        raise ValueError("Choose a symbol, e.g. NIFTY or RELIANCE")
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {', '.join(CONDITIONS)}")
    params: dict = {}
    if condition == "INDICATOR":
        rule = rule or {}
        conditions = [c if isinstance(c, Condition) else Condition(**c) for c in rule.get("conditions", [])]
        if not conditions:
            raise ValueError("An indicator alert needs at least one condition, e.g. RSI(14) < 30")
        join = str(rule.get("join", "AND")).upper()
        if join not in JOINS:
            raise ValueError("join must be AND or OR")
        from dataclasses import asdict
        params = {"rule": {"conditions": [asdict(c) for c in conditions], "join": join}}
        target = None
    else:
        if target is None:
            raise ValueError(f"{CONDITIONS[condition]}: give the level (target)")
        target = float(target)
        if condition in PRICE and target <= 0:
            raise ValueError("A price level must be above 0")
    if condition in CHAIN:
        if symbol not in OPTION_INDICES:
            raise ValueError(f"OI and PCR alerts work for {', '.join(OPTION_INDICES)} only")
        if target < 0:
            raise ValueError("OI and PCR levels cannot be negative")
        if condition.startswith("OI"):
            if side not in OI_SIDES:
                raise ValueError("side must be total, ce or pe")
            params = {"side": side}
    return symbol, condition, target, params


# Save a new alert; returns its id.
def add_alert(symbol: str, condition: str, target: float | None = None, rule: dict | None = None,
              side: str = "total", note: str = "", url: str | None = None) -> int:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Instrument, PriceAlert
    symbol, condition, target, params = clean_alert(symbol, condition, target, rule, side)
    with get_session(url) as session:
        instrument = session.scalar(select(Instrument.id).where(Instrument.symbol == symbol).limit(1))
        row = PriceAlert(symbol=symbol, instrument_id=instrument, condition=condition, target_price=target,
                         params=params, note=note.strip() or None)
        session.add(row)
        session.commit()
        log.info("Price alert %s added: %s", row.id, describe(_as_dict(row)))
        return row.id


# One alert row as a plain dict.
def _as_dict(row) -> dict:
    return {"id": row.id, "symbol": row.symbol, "condition": row.condition,
            "target": float(row.target_price) if row.target_price is not None else None,
            "params": dict(row.params or {}), "is_active": row.is_active, "note": row.note,
            "created_at": row.created_at, "last_checked_at": row.last_checked_at,
            "last_value": float(row.last_value) if row.last_value is not None else None,
            "triggered_at": row.triggered_at, "trigger_message": row.trigger_message}


# Readable text of an alert, e.g. "NIFTY price above 25,500" or "TCS: RSI(14) < 30".
def describe(alert: dict) -> str:
    c, target, sym = alert["condition"], alert.get("target"), alert["symbol"]
    if c == "INDICATOR":
        from marketlens.signals.rules import Condition
        rule = alert["params"].get("rule", {})
        parts = [Condition(**x).label() for x in rule.get("conditions", [])]
        return f"{sym}: " + f" {rule.get('join', 'AND')} ".join(parts)
    if c in CHANGE:
        return f"{sym} {CONDITIONS[c]} {target:+g} %"
    if c.startswith("OI"):
        side = OI_SIDES[alert["params"].get("side", "total")]
        return f"{sym} {side} {CONDITIONS[c]} {target:,.0f}"
    if c.startswith("PCR"):
        return f"{sym} {CONDITIONS[c]} {target:g}"
    return f"{sym} {CONDITIONS[c]} {target:,.2f}"


# Every alert (or only the active ones), newest first, with its description.
def list_alerts(active_only: bool = False, url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import PriceAlert
    query = select(PriceAlert).order_by(PriceAlert.id.desc())
    if active_only:
        query = query.where(PriceAlert.is_active)
    with get_session(url) as session:
        rows = [_as_dict(r) for r in session.scalars(query)]
    for r in rows:
        r["description"] = describe(r)
    columns = ["id", "symbol", "condition", "target", "description", "is_active", "last_value", "last_checked_at",
               "triggered_at", "trigger_message", "note", "params", "created_at"]
    return pd.DataFrame(rows, columns=columns)


# Delete one alert; True when it existed.
def delete_alert(alert_id: int, url: str | None = None) -> bool:
    from marketlens.db import get_session
    from marketlens.models import PriceAlert
    with get_session(url) as session:
        row = session.get(PriceAlert, alert_id)
        if row is None:
            return False
        session.delete(row)
        session.commit()
    log.info("Price alert %s deleted", alert_id)
    return True


# ---------------------------------------------------------------- market data (tests pass their own)

# Yahoo Finance ticker of one of our symbols.
def yahoo_ticker(symbol: str) -> str:
    from marketlens.ingest.prices import yf_ticker
    return INDEX_TICKERS.get(symbol, yf_ticker(symbol))


# Last price and yesterday's close of each symbol: {symbol: (last, previous close)}. Dhan first, then yfinance.
def latest_quotes(symbols: list[str]) -> dict:
    from marketlens.broker.dhan_data import DhanDataClient
    client = DhanDataClient()
    if client.is_ready():
        try:
            table = client.get_quote(symbols)
            return {r.symbol: (float(r.ltp), float(r.close)) for r in table.itertuples()}
        except Exception as err:
            log.warning("Dhan quotes failed (%s); using yfinance", err)
    from marketlens.overview import _yf_closes, closes_from_yf
    tickers = {s: yahoo_ticker(s) for s in symbols}
    data = _yf_closes(sorted(set(tickers.values()) | {"^NSEI"}))     # 2+ tickers keep yfinance's table shape
    return {s: closes_from_yf(data, t) for s, t in tickers.items()}


# About a year of daily candles (open, high, low, close, volume; lowercase like the rules expect).
def daily_candles(symbol: str) -> pd.DataFrame:
    from marketlens.data import download_history
    candles = download_history(yahoo_ticker(symbol), period="1y")
    return candles.rename(columns=str.lower)


# The option chain of the nearest expiry: (table, source "DHAN" or "DEMO").
def nearest_chain(index: str, today: dt.date | None = None) -> tuple[pd.DataFrame, str]:
    from marketlens.broker.market_data import get_data_client
    client = get_data_client()
    today = today or dt.datetime.now(IST).date()
    expiries = [e for e in client.get_expiry_list(index) if e >= today]
    if not expiries:
        raise ValueError(f"No live expiry for {index}")
    chain = client.get_option_chain(index, expiries[0])
    return chain.table, chain.source


# ---------------------------------------------------------------- checking

# The value an alert watches and whether it is hit: (value, hit). value None = no data.
def evaluate(alert: dict, quote=None, candles: pd.DataFrame | None = None, chain: pd.DataFrame | None = None):
    c, target = alert["condition"], alert["target"]
    if c in PRICE + CHANGE:
        last, prev = quote if quote else (None, None)
        if last is None or (c in CHANGE and not prev):
            return None, False
        value = last if c in PRICE else (last / prev - 1) * 100
        return round(value, 2), value >= target if c in ("ABOVE", "CHANGE_ABOVE") else value <= target
    if c == "INDICATOR":
        from marketlens.signals.rules import Condition, evaluate as rule_true
        if candles is None or candles.empty:
            return None, False
        rule = alert["params"]["rule"]
        hit = bool(rule_true([Condition(**x) for x in rule["conditions"]], rule["join"], candles).iloc[-1])
        return round(float(candles["close"].iloc[-1]), 2), hit
    if chain is None or chain.empty:
        return None, False
    if c.startswith("PCR"):
        from marketlens.options.chain import pcr
        value = pcr(chain)
        if value is None:
            return None, False
    else:
        side = alert["params"].get("side", "total")
        columns = ["ce_oi", "pe_oi"] if side == "total" else [f"{side}_oi"]
        value = float(chain[columns].fillna(0).to_numpy().sum())
    return value, value >= target if c.endswith("ABOVE") else value <= target


# Check every active alert once; triggered ones send Telegram and are switched off. Returns one dict per alert.
def check_alerts(url: str | None = None, now: dt.datetime | None = None, quotes=None, candles=None, chains=None,
                 notify=None) -> list[dict]:
    """quotes(symbols) -> {symbol: (last, prev close)}, candles(symbol) -> DataFrame, chains(index) -> (table, source),
    notify(text, source, event, url) -> bool: the real ones by default (tests pass fakes)."""
    from marketlens.alerts.telegram import notify as telegram_notify
    quotes, candles, chains = quotes or latest_quotes, candles or daily_candles, chains or nearest_chain
    notify = notify or telegram_notify
    now = now or dt.datetime.now(IST)
    alerts = list_alerts(active_only=True, url=url).to_dict("records")
    if not alerts:
        return []

    # Fetch each kind of data once for all the alerts that need it; a failure only affects those alerts.
    def fetch(what, func, keys):
        try:
            return func(keys) if what == "quotes" else {k: func(k) for k in keys}
        except Exception as err:
            log.warning("Price alerts: could not fetch %s for %s: %s", what, keys, err)
            return {"__error__": str(err)}

    quote_data = fetch("quotes", quotes, sorted({a["symbol"] for a in alerts if a["condition"] in PRICE + CHANGE})) \
        if any(a["condition"] in PRICE + CHANGE for a in alerts) else {}
    candle_data, chain_data = {}, {}
    for sym in sorted({a["symbol"] for a in alerts if a["condition"] == "INDICATOR"}):
        candle_data.update(fetch("candles", candles, [sym]))
    for index in sorted({a["symbol"] for a in alerts if a["condition"] in CHAIN}):
        chain_data.update(fetch("option chain", chains, [index]))

    results = []
    for alert in alerts:
        sym, c = alert["symbol"], alert["condition"]
        chain, source = (chain_data.get(sym) or (None, None)) if c in CHAIN else (None, None)
        try:
            value, hit = evaluate(alert, quote=quote_data.get(sym), candles=candle_data.get(sym), chain=chain)
        except Exception as err:
            record_error(__name__, f"Price alert {alert['id']} ({describe(alert)}) could not be checked: {err}",
                         {"alert_id": alert["id"]}, url=url, level="WARNING")
            results.append({"id": alert["id"], "description": describe(alert), "value": None, "triggered": False,
                            "status": "error", "message": str(err)})
            continue
        status, message = "checked", ""
        if value is None:
            status, message = "no data", "No data for this alert right now"
        elif c in CHAIN and source == "DEMO":
            status, message, hit = "demo", "DEMO option chain (Dhan keys not set): OI / PCR alerts need Dhan", False
        if hit:
            status = "triggered"
            message = _trigger_text(alert, value)
            notify(message, source="price_alert", event="price_alert", url=url)
        _save_check(alert["id"], value, now, hit, message if hit else None, url)
        results.append({"id": alert["id"], "description": describe(alert), "value": value, "triggered": hit,
                        "status": status, "message": message})
    log.info("Price alerts checked: %d active, %d triggered", len(results), sum(r["triggered"] for r in results))
    return results


# The Telegram text of a triggered alert.
def _trigger_text(alert: dict, value: float) -> str:
    c = alert["condition"]
    shown = (f"{value:+.2f} %" if c in CHANGE else f"{value:,.0f}" if c.startswith("OI")
             else f"{value:g}" if c.startswith("PCR") else f"{value:,.2f}")
    what = "close" if c == "INDICATOR" else "now"
    note = f"\nNote: {alert['note']}" if alert.get("note") else ""
    return f"🔔 Price alert: {describe(alert)} ({what} {shown}){note}"


# Remember what a check saw; a triggered alert is switched off with its message.
def _save_check(alert_id: int, value, now: dt.datetime, hit: bool, message: str | None, url: str | None):
    from marketlens.db import get_session
    from marketlens.models import PriceAlert
    with get_session(url) as session:
        row = session.get(PriceAlert, alert_id)
        row.last_checked_at = now
        if value is not None:
            row.last_value = value
        if hit:
            row.is_active, row.triggered_at, row.trigger_message = False, now, message
        session.commit()


# ---------------------------------------------------------------- command line

# python -m marketlens.alerts.price_alerts list | check | watch [--every 60] | delete ID
def main(argv: list[str] | None = None, url: str | None = None) -> int:
    parser = argparse.ArgumentParser(description="Price alerts on Telegram.")
    parser.add_argument("command", choices=["list", "check", "watch", "delete"])
    parser.add_argument("alert_id", nargs="?", type=int, help="the alert to delete")
    parser.add_argument("--every", type=int, default=WATCH_SECONDS, help="watch: seconds between checks")
    args = parser.parse_args(argv)
    if args.command == "list":
        table = list_alerts(url=url)
        print("No alerts." if table.empty else table[["id", "description", "is_active", "last_value",
                                                      "triggered_at"]].to_string(index=False))
        return 0
    if args.command == "delete":
        if args.alert_id is None:
            parser.error("delete needs the alert id")
        print("Deleted." if delete_alert(args.alert_id, url) else f"No alert {args.alert_id}.")
        return 0
    while True:
        from marketlens.trading.live_runner import beat
        from marketlens.trading.order_service import market_is_open
        now = dt.datetime.now(IST)
        if args.command == "check" or market_is_open(now):
            for r in check_alerts(url, now):
                print(f"{now:%H:%M:%S} #{r['id']} {r['description']}: {r['status']} {r['value'] if r['value'] is not None else ''}",
                      flush=True)
        if args.command == "check":
            return 0
        try:
            beat(HEARTBEAT, f"watching every {args.every} s", url)
        except Exception as err:
            log.warning("Could not write the price-alert heartbeat: %s", err)
        time.sleep(max(args.every, 10))


if __name__ == "__main__":
    raise SystemExit(main())
