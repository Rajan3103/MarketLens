"""Connection to Dhan through the official dhanhq library.

Every other file gets Dhan from here, so keys, retries and speed limits are
handled in one place. Without keys in .env, get_dhan() returns None and the
pages fall back to clearly labelled DEMO data.
"""
from marketlens import config
from marketlens.logger import get_logger
from marketlens.utils import RateLimiter, retry

log = get_logger(__name__)

# Dhan allows a limited number of data requests per second; stay at 5
DATA_LIMITER = RateLimiter(5)

# Dhan error codes that mean "try again later" (too many requests / busy)
TEMPORARY_CODES = {"DH-904", "805", "DH-908"}


class DhanTemporaryError(Exception):
    """Network problem or rate limit: worth trying again (used by @retry)."""


class DhanError(Exception):
    """A problem that will not go away by retrying, e.g. wrong token or wrong input."""


# True when both Dhan keys are filled in in .env.
def has_keys() -> bool:
    """Checks DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN without showing them."""
    return config.is_set(config.DHAN_CLIENT_ID) and config.is_set(config.DHAN_ACCESS_TOKEN)


# Build the dhanhq client, or return None when the keys are not set.
def get_dhan():
    """Created fresh each time so a new token in .env is used after a restart."""
    if not has_keys():
        return None
    from dhanhq import DhanContext, dhanhq
    return dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))


# Turn dhanhq's {"status", "remarks", "data"} answer into data or a clear error.
def unwrap(response: dict) -> dict:
    """Returns response["data"]; raises DhanTemporaryError or DhanError on failure."""
    if response.get("status") == "success":
        return response.get("data") or {}
    remarks = response.get("remarks")
    if isinstance(remarks, dict):
        code = str(remarks.get("error_code") or "")
        message = f"{code} {remarks.get('error_message') or remarks.get('error_type') or ''}".strip()
        if code in TEMPORARY_CODES:
            raise DhanTemporaryError(f"Dhan is busy or rate-limited ({message})")
        if code in ("DH-901", "DH-902"):
            raise DhanError(f"Dhan rejected the access token ({message}). "
                            "Paste a fresh DHAN_ACCESS_TOKEN in .env and restart the app.")
        raise DhanError(f"Dhan error: {message}")
    # A plain text remark means the request never reached Dhan (network problem)
    raise DhanTemporaryError(f"Could not reach Dhan: {remarks}")


# One daily-candle request (tried again on network errors / rate limits).
@retry(exceptions=(DhanTemporaryError,))
def fetch_daily(dhan, security_id: str, segment: str, instrument: str,
                from_date: str, to_date: str) -> dict:
    """Dates as YYYY-MM-DD. Dhan does NOT include to_date in the answer."""
    with DATA_LIMITER:
        return unwrap(dhan.historical_daily_data(security_id, segment, instrument,
                                                 from_date, to_date, oi=instrument.startswith("FUT")))


# One intraday-candle request (tried again on network errors / rate limits).
@retry(exceptions=(DhanTemporaryError,))
def fetch_intraday(dhan, security_id: str, segment: str, instrument: str,
                   from_date: str, to_date: str, interval: int) -> dict:
    """interval: 1, 5, 15, 25 or 60 minutes. At most 90 days per request."""
    with DATA_LIMITER:
        return unwrap(dhan.intraday_minute_data(security_id, segment, instrument, from_date,
                                                to_date, interval=interval,
                                                oi=instrument.startswith("FUT")))
