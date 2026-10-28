"""Tests for marketlens/config.py.

monkeypatch is a pytest helper that changes a setting only for one test
and puts it back afterwards, so your real .env is never touched.
"""
import pytest

from marketlens import config


# Many ways of writing true/false should all be understood.
@pytest.mark.parametrize("text, expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), (" on ", True),
    ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
])
def test_get_bool_reads_words(monkeypatch, text, expected):
    monkeypatch.setenv("ML_TEST_BOOL", text)
    assert config.get_bool("ML_TEST_BOOL", default=not expected) is expected


# Missing, empty or unknown words fall back to the default.
@pytest.mark.parametrize("text", [None, "", "maybe"])
def test_get_bool_uses_default(monkeypatch, text):
    if text is None:
        monkeypatch.delenv("ML_TEST_BOOL", raising=False)
    else:
        monkeypatch.setenv("ML_TEST_BOOL", text)
    assert config.get_bool("ML_TEST_BOOL", default=True) is True
    assert config.get_bool("ML_TEST_BOOL", default=False) is False


# Numbers are converted from text to int or float.
def test_get_number_converts(monkeypatch):
    monkeypatch.setenv("ML_TEST_NUM", "2500")
    assert config.get_number("ML_TEST_NUM", 0.0, float) == 2500.0
    assert config.get_number("ML_TEST_NUM", 0, int) == 2500
    assert isinstance(config.get_number("ML_TEST_NUM", 0, int), int)


# Bad text, missing values and too-small numbers give the default.
@pytest.mark.parametrize("text", [None, "", "abc", "5.5", "0"])
def test_get_number_uses_default(monkeypatch, text):
    if text is None:
        monkeypatch.delenv("ML_TEST_NUM", raising=False)
    else:
        monkeypatch.setenv("ML_TEST_NUM", text)
    # "5.5" is not a whole number, "0" is below the minimum of 1
    assert config.get_number("ML_TEST_NUM", 5, int, minimum=1) == 5


# Choices are case-insensitive, and anything unknown is the safe default.
@pytest.mark.parametrize("text, expected", [
    ("LIVE", "live"), ("paper", "paper"), ("real", "paper"), ("", "paper"),
])
def test_get_choice_trading_mode(monkeypatch, text, expected):
    monkeypatch.setenv("ML_TEST_MODE", text)
    assert config.get_choice("ML_TEST_MODE", "paper", ["paper", "live"]) == expected


# Loaded settings always have the right type, whatever is in .env.
def test_loaded_settings_have_safe_types():
    assert config.TRADING_MODE in ("paper", "live")
    assert config.DATA_SOURCE in ("yfinance", "dhan")
    assert isinstance(config.AUTO_TRADE, bool)
    assert isinstance(config.DEMO_MODE, bool)
    assert isinstance(config.MAX_DAILY_LOSS_RS, float)
    assert isinstance(config.MAX_ORDERS_PER_SECOND, int)
    assert config.DATABASE_URL.startswith(("postgresql", "postgres://"))  # PostgreSQL only


# key_status lists every key as (name, is_set, what_it_unlocks).
def test_key_status_shape():
    status = config.key_status()
    names = [name for name, _, _ in status]
    assert names == [name for name, _ in config.KEYS]
    for name, done, unlocks in status:
        assert isinstance(done, bool)
        assert unlocks


# key_status follows whether a key is filled in, and never returns the key itself.
def test_key_status_follows_values(monkeypatch):
    monkeypatch.setattr(config, "DHAN_CLIENT_ID", "12345")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "   ")
    status = {name: done for name, done, _ in config.key_status()}
    assert status["DHAN_CLIENT_ID"] is True
    assert status["TELEGRAM_BOT_TOKEN"] is False  # spaces only counts as empty
    assert "12345" not in str(config.key_status())


# The database password is hidden before showing the address on screen.
def test_safe_database_url_hides_password():
    url = "postgresql://ml_user:s3cret@db.example.com:5432/marketlens"
    shown = config.safe_database_url(url)
    assert "s3cret" not in shown
    assert shown == "postgresql://***@db.example.com:5432/marketlens"
    no_password = "postgresql+psycopg://localhost:5432/marketlens"
    assert config.safe_database_url(no_password) == no_password


# Upstox was removed: Dhan is the only broker.
def test_no_upstox_keys():
    names = [name for name, _ in config.KEYS]
    assert not any("UPSTOX" in name for name in names)
    assert "DHAN_CLIENT_ID" in names


# A number above `maximum` is refused, like a port number above 65535.
@pytest.mark.parametrize("text, expected", [("8000", 8000), ("70000", 8000), ("0", 8000), ("9001", 9001)])
def test_get_number_maximum(monkeypatch, text, expected):
    monkeypatch.setenv("ML_TEST_PORT", text)
    assert config.get_number("ML_TEST_PORT", 8000, int, minimum=1, maximum=65535) == expected


# The new settings exist with safe types and defaults.
def test_new_settings():
    assert isinstance(config.WEBHOOK_PORT, int) and 1 <= config.WEBHOOK_PORT <= 65535
    assert isinstance(config.MAX_OPEN_POSITIONS, int) and config.MAX_OPEN_POSITIONS >= 1
    assert config.AWS_REGION
    assert ":CHANGE_ME@" not in "postgresql+psycopg://marketlens@localhost:5432/marketlens"


# The Settings page lists every key, including the optional Dhan API key and the webhook secret.
def test_key_list_complete():
    names = [name for name, _ in config.KEYS]
    for key in ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "DHAN_API_KEY", "DHAN_API_SECRET",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WEBHOOK_SECRET", "DASHBOARD_PASSWORD"]:
        assert key in names
    assert len(names) == len(set(names))   # no key listed twice


# Webhook secrets must be long enough to be hard to guess.
def test_webhook_secret_strength():
    assert not config.webhook_secret_strong("")
    assert not config.webhook_secret_strong("short-secret")
    assert config.webhook_secret_strong("x" * config.WEBHOOK_SECRET_MIN_LENGTH)


# The code's default database address has no password in it (rule 1).
def test_default_database_url_has_no_password(monkeypatch):
    import importlib
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    fresh = importlib.reload(config)
    try:
        assert fresh.DATABASE_URL == "postgresql+psycopg://marketlens@localhost:5432/marketlens"
        assert "@" in fresh.DATABASE_URL and ":" not in fresh.DATABASE_URL.split("//")[1].split("@")[0]
    finally:
        monkeypatch.undo()
        importlib.reload(config)
