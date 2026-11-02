"""Pick the broker: PaperBroker (virtual money) unless TRADING_MODE=live, then DhanBroker (real money).

    from marketlens.broker.factory import get_broker
    broker = get_broker(strategy_id=3)
    broker.place_order("RELIANCE", "BUY", 10)

The one place that decides between paper and live, so no strategy or page can send a real order
by accident. DhanBroker checks the keys and TRADING_MODE again before every call.
"""
from marketlens import config
from marketlens.broker.base import Broker
from marketlens.logger import get_logger

log = get_logger(__name__)


# PaperBroker by default; DhanBroker only when TRADING_MODE=live (it raises if the Dhan keys are missing).
def get_broker(strategy_id: int | None = None, url: str | None = None, **kwargs) -> Broker:
    """kwargs go to the broker (e.g. capital=, prices= for paper; dhan= for live tests)."""
    if config.TRADING_MODE == "live":
        from marketlens.broker.dhan_broker import DhanBroker
        log.warning("TRADING_MODE=live: using the Dhan broker (REAL orders)")
        return DhanBroker(url=url, strategy_id=strategy_id, **kwargs)
    from marketlens.broker.paper import PaperBroker
    return PaperBroker(strategy_id=strategy_id, url=url, **kwargs)
