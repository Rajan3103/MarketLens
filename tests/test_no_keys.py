"""Everything must work with NO keys (rule 1): every Streamlit page and every API route, with DEMO_MODE=true,
no Dhan / Telegram / webhook keys and no dashboard password. Pages may show DEMO data or a 'key not set'
message, but never crash; API routes may answer 4xx with a readable message, but never 500.

Runs on TEST_DATABASE_URL (skipped without it); yfinance is replaced by made-up prices, so there is no internet.
"""
from pathlib import Path

import pytest
import yfinance
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from marketlens import config
from marketlens.api import main as api_main
from tests.test_api import fake_prices

ROOT = Path(__file__).resolve().parent.parent
PAGES = sorted(p.name for p in (ROOT / "dashboards").glob("*.py"))
NO_KEYS = {"DHAN_CLIENT_ID": "", "DHAN_ACCESS_TOKEN": "", "DHAN_API_KEY": "", "DHAN_API_SECRET": "",
           "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "WEBHOOK_SECRET": "", "DASHBOARD_PASSWORD": "",
           "DEMO_MODE": True, "TRADING_MODE": "paper"}


@pytest.fixture
def no_keys(db_url, db_session, monkeypatch):
    """No keys at all, DEMO data on, the test database as 'the' database, made-up Yahoo prices."""
    for name, value in NO_KEYS.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "DATABASE_URL", db_url)
    monkeypatch.setattr(yfinance, "download", lambda tickers, **kw: fake_prices())
    return db_url


# Every Streamlit page opens without an error.
@pytest.mark.parametrize("page", PAGES)
def test_streamlit_page_without_keys(no_keys, page):
    at = AppTest.from_file(str(ROOT / "dashboards" / page), default_timeout=120).run()
    assert not at.exception, f"{page}: {[e.value for e in at.exception]}"


# Every GET route answers without a server error (4xx with a message is fine, e.g. 'not found').
GET_ROUTES = [
    "/api/health", "/health", "/api/settings/status",
    "/api/market/history?symbol=RELIANCE.NS", "/api/market/history/download?symbol=RELIANCE.NS",
    "/api/market/snapshot", "/api/market/sectors",
    "/api/jobs/no-such-job",
    "/api/momentum/options", "/api/momentum/ranks?universe=nifty50", "/api/momentum/runs",
    "/api/momentum/runs/9999/rebalances", "/api/momentum/strategies/9999",
    "/api/live/status", "/api/live/strategies", "/api/live/strategies/9999/check", "/api/live/deployments",
    "/api/live/monitor", "/api/live/limits", "/api/live/errors", "/api/live/settings",
    "/webhook/health",
]


@pytest.mark.parametrize("path", GET_ROUTES)
def test_get_route_without_keys(no_keys, path):
    client = TestClient(api_main.create_app(url=no_keys))
    answer = client.get(path)
    assert answer.status_code < 500, f"{path}: {answer.status_code} {answer.text[:300]}"
    if answer.status_code >= 400:
        assert "error" in answer.json()                                   # always a readable message


# POST routes that are safe to call: they refuse politely or work in paper / DEMO mode.
POST_ROUTES = [
    ("/api/momentum/data-check", {"settings": {"universe": "nifty50"}}),
    ("/api/live/login/check", None),
    ("/api/live/kill", {}),                                                  # refused: no confirm word
    ("/api/live/square-off", {}),                                            # refused: no confirm word
    ("/api/live/signals/9999/reject", None),
    ("/api/jobs/no-such-job/cancel", None),
    ("/webhook/tradingview", {"secret": "x", "action": "BUY", "symbol": "RELIANCE"}),
    ("/webhook/chartink", {"secret": "x", "stocks": "RELIANCE"}),
]


@pytest.mark.parametrize("path, body", POST_ROUTES)
def test_post_route_without_keys(no_keys, path, body):
    client = TestClient(api_main.create_app(url=no_keys))
    answer = client.post(path, json=body) if body is not None else client.post(path)
    assert answer.status_code < 500, f"{path}: {answer.status_code} {answer.text[:300]}"


# The live settings route (the one route that had no test): the page's help values, never a key.
def test_live_settings_route(no_keys):
    body = TestClient(api_main.create_app(url=no_keys)).get("/api/live/settings").json()
    assert body["TRADING_MODE"] == "paper" and body["AUTO_TRADE"] is False and body["MAX_DAILY_LOSS_RS"] > 0
    assert not any("TOKEN" in k or "SECRET" in k or "PASSWORD" in k for k in body)
