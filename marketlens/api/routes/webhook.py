"""The webhook receiver's endpoints (/webhook/tradingview, /webhook/chartink, /webhook/health).

They live in marketlens/webhook/server.py; this file only hands them to the API app.
"""
from marketlens.webhook.server import router  # noqa: F401  (found by main.find_routers)
