"""The MarketLens webhook format: what TradingView and Chartink alerts send, and how it is checked.

TradingView (the Pine Script export builds this message; you can also paste it by hand):
    POST http://<server>:8000/webhook/tradingview
    {"secret": "<WEBHOOK_SECRET>", "strategy": "EMA 9/21", "symbol": "RELIANCE", "action": "BUY",
     "qty": 10, "price": 2501.5, "note": "EMA cross"}
  action: BUY, SELL or EXIT (close the position). qty = units, or lots = lots (x the lot size).
  Optional: exchange, timeframe, time.

Chartink cannot add a secret to its message, so the secret goes in the address instead:
    POST http://<server>:8000/webhook/chartink?secret=<WEBHOOK_SECRET>
    {"stocks": "RELIANCE,TCS", "trigger_prices": "2501.5,3900.1", "triggered_at": "10:15 am",
     "scan_name": "EMA cross", "scan_url": "ema-cross", "alert_name": "EMA cross alert"}

The real secret lives only in .env (WEBHOOK_SECRET) and in your TradingView / Chartink alert.
It is never shown on the page or written in exported code (a placeholder is used instead).
"""
import hmac

import pandas as pd

from marketlens import config

PATHS = {"tradingview": "/webhook/tradingview", "chartink": "/webhook/chartink"}
SECRET_PLACEHOLDER = "PASTE_YOUR_WEBHOOK_SECRET"
ACTIONS = ("BUY", "SELL", "EXIT")


class SecretError(ValueError):
    """The secret is wrong or missing (the server answers 401)."""


# The address alerts are sent to: this computer now, the AWS server's static IP once it is set.
def base_url() -> str:
    host = config.SERVER_STATIC_IP if config.is_set(config.SERVER_STATIC_IP) else "localhost"
    return f"http://{host}:{config.WEBHOOK_PORT}"


# The full webhook addresses (Chartink's has the secret placeholder in it).
def webhook_urls() -> dict:
    return {"tradingview": base_url() + PATHS["tradingview"],
            "chartink": base_url() + PATHS["chartink"] + f"?secret={SECRET_PLACEHOLDER}"}


# True when WEBHOOK_SECRET is set and long enough.
def secret_ready() -> bool:
    return config.webhook_secret_strong()


# The JSON message to paste into a TradingView alert (secret as a placeholder).
def tradingview_message(strategy: str = "My strategy", symbol: str = "{{ticker}}", action: str = "BUY",
                        qty: int = 1, note: str = "TradingView alert") -> dict:
    """With symbol "{{ticker}}" TradingView fills in the chart's symbol; price uses {{close}}."""
    return {"secret": SECRET_PLACEHOLDER, "strategy": strategy, "symbol": symbol, "action": action, "qty": qty,
            "price": "{{close}}", "note": note, "exchange": "{{exchange}}", "timeframe": "{{interval}}",
            "time": "{{timenow}}"}


# An example of what Chartink sends (you do not type this; Chartink builds it).
CHARTINK_EXAMPLE = {"stocks": "RELIANCE,TCS", "trigger_prices": "2501.5,3900.1", "triggered_at": "10:15 am",
                    "scan_name": "EMA cross", "scan_url": "ema-cross", "alert_name": "EMA cross alert",
                    "webhook_url": "http://localhost:8000/webhook/chartink?secret=..."}


# Compare secrets without leaking how many characters matched (constant-time).
def _same(given, expected: str) -> bool:
    return bool(expected) and isinstance(given, str) and hmac.compare_digest(given.encode(), expected.encode())


# The lot size for lots -> qty: NIFTY 65, BANKNIFTY 30 ... (the first word of an option / future symbol).
def lot_size_of(symbol: str) -> int:
    from marketlens.options.chain import DEFAULT_LOT_SIZES
    return DEFAULT_LOT_SIZES.get(symbol.split()[0].upper(), 1) if symbol.strip() else 1


# Check a TradingView message and return it tidy; raises SecretError (401) or ValueError (400).
def parse_tradingview(payload: dict, secret: str | None = None) -> dict:
    secret = config.WEBHOOK_SECRET if secret is None else secret
    if not isinstance(payload, dict):
        raise ValueError("The message must be JSON (an object)")
    if not _same(payload.get("secret"), secret):
        raise SecretError("Wrong or missing secret")
    action = str(payload.get("action", "")).upper()
    if action in ("ENTRY", "") and str(payload.get("side", "")).upper() in ("BUY", "SELL"):
        action = str(payload["side"]).upper()                 # the older side + action shape
    symbol = str(payload.get("symbol", "")).strip().upper()
    if action not in ACTIONS:
        raise ValueError("action must be BUY, SELL or EXIT")
    if not symbol or "{{" in symbol:
        raise ValueError("symbol is missing")
    try:
        lots = int(float(payload["lots"])) if payload.get("lots") not in (None, "") else None
        qty = int(float(payload["qty"])) if payload.get("qty") not in (None, "") else None
        price = float(payload["price"]) if payload.get("price") not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("qty, lots and price must be numbers")
    if qty is None:
        qty = (lots or 1) * lot_size_of(symbol)
    if qty < 1 or (lots is not None and lots < 1):
        raise ValueError("qty / lots must be 1 or more")
    if price is not None and price <= 0:
        raise ValueError("price must be above 0")
    return {"source": "tradingview", "strategy": str(payload.get("strategy", "")).strip(), "symbol": symbol,
            "exchange": str(payload.get("exchange", "NSE")).upper(), "action": action, "qty": qty, "lots": lots,
            "price": price, "note": str(payload.get("note", "")), "timeframe": str(payload.get("timeframe", "")),
            "time": str(payload.get("time", ""))}


# Check a Chartink message (secret from the address) and return one signal per stock.
def parse_chartink(payload: dict, query_secret: str | None, secret: str | None = None) -> list[dict]:
    secret = config.WEBHOOK_SECRET if secret is None else secret
    if not _same(query_secret, secret):
        raise SecretError("Wrong or missing secret (add ?secret=... to the Chartink webhook address)")
    stocks = [s.strip().upper() for s in str(payload.get("stocks", "")).split(",") if s.strip()]
    prices = [p.strip() for p in str(payload.get("trigger_prices", "")).split(",")]
    if not stocks:
        raise ValueError("No stocks in the Chartink message")
    out = []
    for i, sym in enumerate(stocks):
        try:
            price = float(prices[i]) if i < len(prices) and prices[i] else None
        except ValueError:
            price = None
        out.append({"source": "chartink", "strategy": str(payload.get("scan_name", "")).strip(), "symbol": sym,
                    "exchange": "NSE", "action": "BUY", "qty": 1, "lots": None, "price": price,
                    "note": str(payload.get("alert_name", "Chartink scan")), "timeframe": "",
                    "time": str(payload.get("triggered_at", ""))})
    return out


# The latest webhook events (newest first), with the secret hidden.
def recent_events(limit: int = 50, url: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import WebhookEvent
    with get_session(url) as session:
        rows = session.scalars(select(WebhookEvent).order_by(WebhookEvent.id.desc()).limit(limit)).all()
        data = [{"received": r.received_at, "source": r.source, "processed": r.processed, "error": r.error,
                 "message": {k: ("***" if k == "secret" else v) for k, v in (r.payload or {}).items()}}
                for r in rows]
    return pd.DataFrame(data, columns=["received", "source", "processed", "error", "message"])
