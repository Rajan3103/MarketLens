"""The webhook receiver: TradingView and Chartink alerts arrive here (module 7).

Start it (it keeps running until Ctrl+C):
    python -m marketlens.webhook.server                 # port from WEBHOOK_PORT (8000)

    POST /webhook/tradingview                 JSON {secret, strategy, symbol, action BUY/SELL/EXIT, qty or lots, price, note}
    POST /webhook/chartink?secret=<secret>    Chartink's scan alert (stocks, trigger_prices, scan_name): one signal per stock
    GET  /health                              {"status": "ok"}

For every alert:
  1. a wrong or missing secret -> 401 (logged, and saved as a rejected event);
  2. the same alert again within 10 seconds -> ignored (TradingView sometimes sends twice);
  3. saved in webhook_events, one row in signals per symbol;
  4. a Telegram message (when the Telegram keys are set);
  5. if the strategy named in the alert is set to "auto" in the Strategy Library, an order through
     the order service: paper (virtual money) by default.

TradingView only sends alerts to a public internet address (port 80 / 443), so real alerts arrive
after the AWS step; on this computer, test it with a fake alert (see README).
"""
import argparse
import datetime as dt
import json
import time

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from marketlens import config
from marketlens.broker.candles import IST
from marketlens.logger import get_logger
from marketlens.webhook import payload as wh

log = get_logger(__name__)
DUPLICATE_SECONDS = 10


# The message without its secret (what is stored and shown).
def masked(message) -> dict:
    if not isinstance(message, dict):
        return {"raw": str(message)[:2000]}
    return {k: ("***" if k == "secret" else v) for k, v in message.items()}


class Receiver:
    """Everything the endpoints do, kept outside FastAPI so it is easy to test."""

    def __init__(self, url: str | None = None, clock=time.monotonic, notify=None, execute=None):
        from marketlens.alerts.telegram import send_message
        from marketlens.trading.orders import execute_signal
        self.url, self.clock = url, clock
        self.notify = notify or send_message
        self.execute = execute or execute_signal
        self.recent: dict[tuple, float] = {}

    # True when the same alert came in the last 10 seconds (otherwise it is remembered).
    def is_duplicate(self, key: tuple) -> bool:
        now = self.clock()
        self.recent = {k: t for k, t in self.recent.items() if now - t < DUPLICATE_SECONDS}
        if key in self.recent:
            return True
        self.recent[key] = now
        return False

    # Store one webhook event; returns its id.
    def save_event(self, source: str, message, processed: bool, error: str | None = None,
                   signal_ids: list[int] | None = None) -> int:
        from marketlens.db import get_session
        from marketlens.models import WebhookEvent
        body = masked(message)
        if signal_ids:
            body["signal_ids"] = signal_ids
        with get_session(self.url) as session:
            row = WebhookEvent(source=source, payload=body, processed=processed, error=error,
                               signal_id=signal_ids[0] if signal_ids else None)
            session.add(row)
            session.commit()
            return row.id

    # The Strategy Library row named in the alert: (id, auto, product) or (None, False, "INTRADAY").
    def strategy_for(self, name: str):
        from sqlalchemy import func, select

        from marketlens.db import get_session
        from marketlens.models import Strategy
        if not name:
            return None, False, "INTRADAY"
        with get_session(self.url) as session:
            row = session.scalar(select(Strategy).where(func.lower(Strategy.name) == name.lower()))
            if row is None:
                return None, False, "INTRADAY"
            params = row.params or {}
            product = "CNC" if params.get("timeframe") == "1 day" else "INTRADAY"
            return row.id, bool(params.get("auto")), product

    # One signal: save it, maybe trade it, and return (signal id, Telegram text, order answer).
    def handle_signal(self, signal: dict) -> tuple[int, str, dict | None]:
        from sqlalchemy import select

        from marketlens.db import get_session
        from marketlens.models import Instrument, Signal
        strategy_id, auto, product = self.strategy_for(signal["strategy"])
        order = None
        if auto:
            if config.TRADING_MODE == "live" and not config.AUTO_TRADE:
                order = {"status": "SKIPPED", "message": "TRADING_MODE=live needs AUTO_TRADE=true for automatic orders"}
            else:
                try:
                    r = self.execute(signal, strategy_id, product=product, url=self.url)
                    order = {"status": r.status, "order_id": r.order_id, "price": r.avg_price, "message": r.message}
                except Exception as err:
                    order = {"status": "ERROR", "message": str(err)}
        with get_session(self.url) as session:
            instrument = session.scalar(select(Instrument.id).where(Instrument.symbol == signal["symbol"]))
            row = Signal(ts=dt.datetime.now(IST), strategy_id=strategy_id, instrument_id=instrument,
                         side=signal["action"], price=signal.get("price"), source="webhook",
                         reason=signal.get("note") or None,
                         params={**{k: v for k, v in signal.items() if k != "note"}, "auto": auto, "order": order})
            session.add(row)
            session.commit()
            signal_id = row.id
        price = f" at {signal['price']:,.2f}" if signal.get("price") else ""
        text = (f"🔔 {signal['source'].title()} alert: {signal['action']} {signal['qty']} {signal['symbol']}{price}"
                f"{' · ' + signal['strategy'] if signal['strategy'] else ''}"
                f"{' · ' + signal['note'] if signal.get('note') else ''}")
        if order:
            text += f"\nPaper order: {order['status']}" + (f" ({order['message']})" if order.get("message") else "")
        elif strategy_id is None and signal["strategy"]:
            text += "\n(No strategy with this name in the Strategy Library: signal saved only.)"
        return signal_id, text, order

    # Reject, ignore or process one alert; returns (HTTP status, answer, Telegram texts).
    def receive(self, source: str, message, query_secret: str | None = None) -> tuple[int, dict, list[str]]:
        try:
            signals = (wh.parse_tradingview(message) if source == "tradingview"
                       else wh.parse_chartink(message if isinstance(message, dict) else {}, query_secret))
        except wh.SecretError as err:
            log.warning("Webhook %s rejected: %s", source, err)
            self.save_event(source, message, False, f"401 {err}")
            return 401, {"status": "rejected", "error": str(err)}, []
        except ValueError as err:
            log.warning("Webhook %s refused: %s", source, err)
            self.save_event(source, message, False, f"400 {err}")
            return 400, {"status": "refused", "error": str(err)}, []
        signals = signals if isinstance(signals, list) else [signals]
        key = (source, json.dumps([{k: s[k] for k in ("strategy", "symbol", "action", "qty", "price")}
                                   for s in signals], sort_keys=True))
        if self.is_duplicate(key):
            log.info("Webhook %s: duplicate within %s s ignored", source, DUPLICATE_SECONDS)
            self.save_event(source, message, False, f"duplicate within {DUPLICATE_SECONDS} s (ignored)")
            return 200, {"status": "duplicate", "ignored": True}, []
        results = [self.handle_signal(s) for s in signals]
        ids = [r[0] for r in results]
        self.save_event(source, message, True, None, ids)
        return 200, {"status": "ok", "signals": ids, "orders": [r[2] for r in results]}, [r[1] for r in results]


# The FastAPI app (tests pass their own database address, clock, Telegram and order functions).
def create_app(url: str | None = None, clock=time.monotonic, notify=None, execute=None) -> FastAPI:
    app = FastAPI(title="MarketLens webhook receiver", version="1.0")
    app.state.receiver = Receiver(url, clock, notify, execute)

    # Read the body as JSON (None when it is not JSON).
    async def body_of(request: Request):
        try:
            return await request.json()
        except Exception:
            text = (await request.body()).decode(errors="replace")
            return {"raw": text[:2000]} if text else None

    # Send the answer and the Telegram messages (after answering, so TradingView never waits).
    def answer(status: int, body: dict, texts: list[str], background: BackgroundTasks) -> JSONResponse:
        for text in texts:
            background.add_task(app.state.receiver.notify, text)
        return JSONResponse(body, status_code=status, background=background)

    @app.get("/health")
    def health():
        """Is the receiver running?"""
        return {"status": "ok", "time": dt.datetime.now(IST).isoformat(timespec="seconds"),
                "secret_set": wh.secret_ready()}

    @app.post("/webhook/tradingview")
    async def tradingview(request: Request, background: BackgroundTasks):
        """TradingView alert: {secret, strategy, symbol, action, qty or lots, price, note}."""
        message = await body_of(request)
        if message is None or "raw" in (message if isinstance(message, dict) else {}):
            app.state.receiver.save_event("tradingview", message, False, "400 not JSON")
            return JSONResponse({"status": "refused", "error": "The message must be JSON"}, status_code=400)
        return answer(*app.state.receiver.receive("tradingview", message), background)

    @app.post("/webhook/chartink")
    async def chartink(request: Request, background: BackgroundTasks, secret: str | None = None):
        """Chartink scan alert; the secret is in the address: /webhook/chartink?secret=..."""
        message = await body_of(request)
        return answer(*app.state.receiver.receive("chartink", message or {}, secret), background)

    return app


app = create_app()


# python -m marketlens.webhook.server [--host 0.0.0.0] [--port 8000]
def main(argv: list[str] | None = None):
    import uvicorn
    public = config.is_set(config.SERVER_STATIC_IP)
    parser = argparse.ArgumentParser(description="Receive TradingView / Chartink webhook alerts.")
    parser.add_argument("--host", default="0.0.0.0" if public else "127.0.0.1",
                        help="127.0.0.1 = this computer only (default); 0.0.0.0 = reachable from outside (server)")
    parser.add_argument("--port", type=int, default=config.WEBHOOK_PORT)
    args = parser.parse_args(argv)
    if not wh.secret_ready():
        log.warning("WEBHOOK_SECRET is not set (or shorter than %s characters): every alert will be rejected.",
                    config.WEBHOOK_SECRET_MIN_LENGTH)
    print(f"Webhook receiver on http://{args.host}:{args.port}  (health check: /health, stop: Ctrl+C)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
