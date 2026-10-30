"""The order service: turns a signal (BUY / SELL / EXIT) into an order on the right broker.

    result = execute_signal({"symbol": "RELIANCE", "action": "BUY", "qty": 10, "price": 2501.5}, strategy_id=4)

Paper (virtual money) by default. With TRADING_MODE=live nothing is sent yet: the live Dhan
broker arrives with module 11, and even then only strategies allowed by AUTO_TRADE=true trade alone.
Each strategy has its own paper account (see PaperBroker strategy_id).
"""
from marketlens import config
from marketlens.broker.base import OrderResult
from marketlens.broker.paper import PaperBroker
from marketlens.logger import get_logger

log = get_logger(__name__)


# The broker for a strategy: the paper broker (live trading is not available yet).
def broker_for(strategy_id: int | None, url: str | None = None) -> PaperBroker:
    if config.TRADING_MODE == "live":
        raise RuntimeError("TRADING_MODE=live, but the live Dhan broker is not built yet (module 11): "
                           "no order was sent. Set TRADING_MODE=paper to practise with virtual money.")
    return PaperBroker(strategy_id=strategy_id, url=url)


# Place the order for one signal. EXIT closes whatever this strategy holds in that symbol.
def execute_signal(signal: dict, strategy_id: int | None = None, product: str = "INTRADAY",
                   url: str | None = None) -> OrderResult:
    broker = broker_for(strategy_id, url)
    symbol, action = signal["symbol"], signal["action"]
    if signal.get("price"):
        broker.update_price(symbol, float(signal["price"]))   # the alert's price is the latest price
    if action == "EXIT":
        held = next((p for (sym, _), p in broker.positions.items() if sym == symbol and p.is_open), None)
        if held is None:
            return OrderResult(None, "REJECTED", message=f"Nothing to exit: no open {symbol} position")
        side = "SELL" if held.qty > 0 else "BUY"
        result = broker.place_order(symbol, side, abs(held.qty), product=held.product, tag="webhook:EXIT")
    else:
        result = broker.place_order(symbol, action, int(signal["qty"]), product=product, tag=f"webhook:{action}")
    log.info("Webhook order for strategy %s: %s %s x%s -> %s %s", strategy_id, action, symbol, signal.get("qty"),
             result.status, result.message)
    return result
