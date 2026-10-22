"""Reads settings and secret keys from the .env file.

Keys are never written in the code itself (requirement NFR-07).
"""
import os

from dotenv import load_dotenv

# Load the values from .env (if the file exists) into the environment
load_dotenv()

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# "paper" (practice) is the safe default; "live" sends real orders
TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()


def is_set(value: str) -> bool:
    """Return True when a setting has been filled in."""
    return bool(value and value.strip())
