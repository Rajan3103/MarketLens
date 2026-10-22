"""Shared setup for all tests. pytest reads this file automatically.

It adds the MarketLens folder to Python's search path so tests can
"import marketlens" no matter which folder pytest is started from.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
