"""One place to set up logging for every part of MarketLens.

Messages go to the terminal and to logs/marketlens.log.
When the log file reaches 5 MB it is renamed to marketlens.log.1 and a new
one is started. Only the 5 newest old files are kept.
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# logs/ folder next to app.py (one level above this marketlens/ folder)
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "marketlens.log"
MAX_BYTES = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 5

# How each line looks, e.g. 2026-10-22 10:15:00 | INFO | marketlens.data | Downloaded TCS.NS
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# Return a logger with the given name that writes to the terminal and the log file.
def get_logger(name: str) -> logging.Logger:
    """Get a ready-to-use logger.

    Usage:
        from marketlens.logger import get_logger
        log = get_logger(__name__)
        log.info("Hello")
    """
    logger = logging.getLogger(name)

    # Streamlit re-runs pages often; only add the handlers the first time
    if logger.handlers:
        return logger

    LOG_DIR.mkdir(exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    terminal = logging.StreamHandler()
    terminal.setFormatter(formatter)

    # encoding="utf-8" so rupee signs and emojis also work on Windows
    log_file = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES,
                                   backupCount=BACKUP_COUNT, encoding="utf-8")
    log_file.setFormatter(formatter)

    logger.addHandler(terminal)
    logger.addHandler(log_file)
    logger.setLevel(logging.INFO)
    # Do not also pass messages to Python's main logger (avoids double lines)
    logger.propagate = False
    return logger


# Write an error to the log file AND the error_log table (the Bot Monitor shows them). Never raises.
def record_error(module: str, message: str, details: dict | None = None, url: str | None = None,
                 level: str = "ERROR"):
    """Example: record_error(__name__, "Dhan refused the order", {"signal_id": 12})"""
    logger = get_logger(module)
    logger.log(logging.WARNING if level == "WARNING" else logging.ERROR, message)
    try:
        from marketlens.db import get_session
        from marketlens.models import ErrorLog
        with get_session(url) as session:
            session.add(ErrorLog(level=level, module=module, message=message, details=details))
            session.commit()
    except Exception as err:                     # no database: the log file still has the error
        logger.warning("Could not save the error in error_log: %s", err)
