"""GET /api/settings/status: which keys are set, PAPER or LIVE, and the database connection.

Only says IF a key is set, never the key itself (same as the Settings & Status page).
"""
from fastapi import APIRouter, Request

from marketlens import config

router = APIRouter(prefix="/api/settings", tags=["settings"])


# Keys, trading mode and database, for the frontend's Settings page.
@router.get("/status")
def settings_status(request: Request) -> dict:
    """Which keys are set (never their values), the trading mode and the database connection."""
    from marketlens.broker.dhan_client import has_keys
    from marketlens.db import connection_status
    ok, message = connection_status(getattr(request.app.state, "url", None))
    return {
        "keys": [{"name": name, "set": is_set, "unlocks": unlocks} for name, is_set, unlocks in config.key_status()],
        "trading_mode": config.TRADING_MODE.upper(),
        "auto_trade": config.AUTO_TRADE,
        "demo": bool(config.DEMO_MODE and not has_keys()),
        "database": {"status": "ok" if ok else "fail", "message": message},   # the password is never shown
    }
