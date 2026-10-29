"""Reads settings and secret keys from the .env file.

Keys are never written in the code itself (requirements FR-32 and NFR-07).
Every setting has a safe default, so the app runs even with no .env file.
Values in .env are always text, so the helpers below turn them into
numbers and True/False.
"""
import os

from dotenv import load_dotenv

from marketlens.logger import get_logger

# Load the values from .env (if the file exists) into the environment
load_dotenv()
log = get_logger(__name__)

# Words accepted for true/false settings (upper or lower case both work)
TRUE_WORDS = {"true", "1", "yes", "on"}
FALSE_WORDS = {"false", "0", "no", "off"}


# Read a text setting; missing or empty gives the default.
def get_str(name: str, default: str = "") -> str:
    """Return the setting as text with spaces trimmed."""
    value = os.getenv(name, "").strip()
    return value if value else default


# Read a true/false setting such as AUTO_TRADE=false.
def get_bool(name: str, default: bool) -> bool:
    """Return True or False. Unknown words give the default and a warning."""
    value = get_str(name).lower()
    if not value:
        return default
    if value in TRUE_WORDS:
        return True
    if value in FALSE_WORDS:
        return False
    log.warning("%s=%r is not true/false, using %s", name, value, default)
    return default


# Read a number setting such as MAX_DAILY_LOSS_RS=2000.
def get_number(name: str, default, cast=float, minimum=None, maximum=None):
    """Return the setting as a number (cast=int for whole numbers).

    Text that is not a number, or a number below `minimum` or above `maximum`,
    gives the default and a warning.
    """
    value = get_str(name)
    if not value:
        return default
    try:
        number = cast(value)
    except ValueError:
        log.warning("%s=%r is not a valid number, using %s", name, value, default)
        return default
    if minimum is not None and number < minimum:
        log.warning("%s=%s is below %s, using %s", name, number, minimum, default)
        return default
    if maximum is not None and number > maximum:
        log.warning("%s=%s is above %s, using %s", name, number, maximum, default)
        return default
    return number


# Read a setting that must be one word from a fixed list, e.g. paper or live.
def get_choice(name: str, default: str, choices: list[str]) -> str:
    """Return the setting in lower case, or the default if it is not in `choices`."""
    value = get_str(name).lower()
    if not value:
        return default
    if value in choices:
        return value
    log.warning("%s=%r must be one of %s, using %s", name, value, choices, default)
    return default


# ---- Dhan: the only broker and market-data API ----
DHAN_CLIENT_ID = get_str("DHAN_CLIENT_ID")
DHAN_ACCESS_TOKEN = get_str("DHAN_ACCESS_TOKEN")      # from web.dhan.co, lasts 24 hours
# Optional: API key + secret (valid 12 months) for the automatic daily login flow
DHAN_API_KEY = get_str("DHAN_API_KEY")
DHAN_API_SECRET = get_str("DHAN_API_SECRET")

# ---- Telegram ----
TELEGRAM_BOT_TOKEN = get_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = get_str("TELEGRAM_CHAT_ID")

# ---- Webhooks (TradingView / Chartink alerts, module 7) ----
WEBHOOK_SECRET = get_str("WEBHOOK_SECRET")           # alerts without this text are rejected
WEBHOOK_PORT = get_number("WEBHOOK_PORT", 8000, int, minimum=1, maximum=65535)
WEBHOOK_SECRET_MIN_LENGTH = 24                       # shorter secrets are easy to guess

# ---- App ----
DASHBOARD_PASSWORD = get_str("DASHBOARD_PASSWORD")
# PostgreSQL only. The password goes in .env, never in the code, so the default has none.
DATABASE_URL = get_str("DATABASE_URL", "postgresql+psycopg://marketlens@localhost:5432/marketlens")
# A separate, empty database used only by the tests (empty = database tests are skipped)
TEST_DATABASE_URL = get_str("TEST_DATABASE_URL")
DATA_SOURCE = get_choice("DATA_SOURCE", "yfinance", ["yfinance", "dhan"])
# Default folder for the Historical Data Downloader (module 3); the page lets you change it
DOWNLOAD_DIR = get_str("DOWNLOAD_DIR", "data/downloads")
DEMO_MODE = get_bool("DEMO_MODE", True)

# ---- Trading safety ----
# "paper" (practice) is the safe default; only the exact word "live" sends real orders
TRADING_MODE = get_choice("TRADING_MODE", "paper", ["paper", "live"])
PAPER_CAPITAL = get_number("PAPER_CAPITAL", 500_000.0, float, minimum=0)    # virtual money for paper trading
AUTO_TRADE = get_bool("AUTO_TRADE", False)
MAX_DAILY_LOSS_RS = get_number("MAX_DAILY_LOSS_RS", 2000.0, float, minimum=0)
MAX_ORDERS_PER_SECOND = get_number("MAX_ORDERS_PER_SECOND", 5, int, minimum=1)
MAX_OPEN_POSITIONS = get_number("MAX_OPEN_POSITIONS", 5, int, minimum=1)

# ---- AWS server (used later, when the app runs on AWS) ----
AWS_REGION = get_str("AWS_REGION", "ap-south-1")      # ap-south-1 = Mumbai
# Dhan only accepts ORDERS from a whitelisted static IP (SEBI rule); reading data works without
SERVER_STATIC_IP = get_str("SERVER_STATIC_IP")

# Every key the Settings page checks, and what it unlocks once filled in
KEYS = [
    ("DHAN_CLIENT_ID", "Dhan: live option chain, expired options data, orders"),
    ("DHAN_ACCESS_TOKEN", "Dhan: needed together with the client ID (renew every 24 hours)"),
    ("DHAN_API_KEY", "Dhan (optional): automatic daily login instead of pasting a token"),
    ("DHAN_API_SECRET", "Dhan (optional): needed together with the API key"),
    ("TELEGRAM_BOT_TOKEN", "Telegram: send alerts and answer bot commands"),
    ("TELEGRAM_CHAT_ID", "Telegram: which chat receives the alerts"),
    ("WEBHOOK_SECRET", "TradingView / Chartink alerts (module 7); alerts without it are rejected"),
    ("DASHBOARD_PASSWORD", "Password lock for the dashboard on a server"),
    ("TEST_DATABASE_URL", "Database tests (pytest) on a separate test database"),
]


# Return True when a setting has been filled in.
def is_set(value: str) -> bool:
    """Return True when a setting has been filled in."""
    return bool(value and value.strip())


# List every key with whether it is set and what it unlocks.
def key_status() -> list[tuple[str, bool, str]]:
    """Return (name, is_set, what_it_unlocks) for each key in KEYS.

    Only says IF a key is set; it never returns the key itself.
    """
    # globals() is this file's own variables, so globals()["DHAN_CLIENT_ID"]
    # is the same as DHAN_CLIENT_ID
    return [(name, is_set(globals()[name]), unlocks) for name, unlocks in KEYS]


# Show the database address with any password replaced by ***.
def safe_database_url(url: str | None = None) -> str:
    """postgresql://user:secret@host/db becomes postgresql://***@host/db."""
    url = DATABASE_URL if url is None else url
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    host = rest.rsplit("@", 1)[1]
    return f"{scheme}://***@{host}"


# True when the webhook secret is long enough to be hard to guess.
def webhook_secret_strong(secret: str | None = None) -> bool:
    """At least WEBHOOK_SECRET_MIN_LENGTH characters (empty = not strong)."""
    secret = WEBHOOK_SECRET if secret is None else secret
    return len(secret.strip()) >= WEBHOOK_SECRET_MIN_LENGTH
