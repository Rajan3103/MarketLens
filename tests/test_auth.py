"""The shared login (marketlens/auth.py), the web login routes (/api/auth/*) and the cookie check in the API.

No internet. Only the webhook test uses the test database (TEST_DATABASE_URL), never the real one.
"""
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marketlens import auth, config
from marketlens.api import main as api_main
from marketlens.api.routes.auth import COOKIE_NAME

PASSWORD = "open sesame"
SECRET = "s" * 40                              # a long enough SESSION_SECRET for tests
PROTECTED = "/api/settings/status"             # any normal /api address


class Clock:
    """A fake clock in seconds that the test moves by hand."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self):
        return self.now


# Turn the password lock on (with a good session secret) and pretend the database is fine.
@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setattr(config, "SESSION_SECRET", SECRET)
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (True, "Connected"))
    monkeypatch.setattr(api_main, "record_error", lambda *a, **k: None)


# A test browser for the API (keeps cookies between requests, like a real browser).
@pytest.fixture
def client(locked):
    return TestClient(api_main.create_app())


# Log in with the right password.
def log_in(client) -> dict:
    answer = client.post("/api/auth/login", json={"password": PASSWORD})
    assert answer.status_code == 200, answer.text
    return answer


# ---------------------------------------------------------------- marketlens/auth.py

# The password check is exact (case, spaces, any language).
def test_password_ok(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", PASSWORD)
    assert auth.password_ok(PASSWORD) and not auth.password_ok("Open sesame") and not auth.password_ok(None)
    assert auth.password_ok("पासवर्ड", "पासवर्ड")


# 5 wrong tries in a minute: a pause until the oldest one is a minute old; other addresses are not affected.
def test_limiter_five_per_minute():
    clock = Clock()
    limiter = auth.LoginLimiter(clock=clock)
    for _ in range(5):
        assert limiter.wait_seconds("1.2.3.4") == 0
        limiter.wrong("1.2.3.4")
        clock.now += 1                                   # tries at 1000, 1001, ... 1004
    assert limiter.wait_seconds("1.2.3.4") == pytest.approx(55)      # 1000 + 60 - 1005
    assert limiter.wait_seconds("5.6.7.8") == 0
    clock.now = 1060                                     # the first wrong try is now a minute old
    assert limiter.wait_seconds("1.2.3.4") == 0
    limiter.reset("1.2.3.4")
    assert limiter.wait_seconds("1.2.3.4") == 0


# A token works until it expires, and not after logout, tampering, or a new secret or password.
def test_session_tokens(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setattr(config, "SESSION_SECRET", SECRET)
    token = auth.make_session_token(now=0)
    assert auth.session_ok(token, now=11 * 3600)
    assert not auth.session_ok(token, now=12 * 3600)                  # 12 hours later: expired
    expires, token_id, signature = token.split(".")
    assert not auth.session_ok(f"{int(expires) + 3600}.{token_id}.{signature}", now=0)   # longer life: bad signature
    assert not auth.session_ok("", now=0) and not auth.session_ok("not.a.token", now=0)
    monkeypatch.setattr(config, "SESSION_SECRET", "t" * 40)
    assert not auth.session_ok(token, now=0)                          # another secret
    monkeypatch.setattr(config, "SESSION_SECRET", SECRET)
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "new password")
    assert not auth.session_ok(token, now=0)                          # the password was changed
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", PASSWORD)
    auth.forget_session(token, now=0)
    assert not auth.session_ok(token, now=0)                          # logged out


# A password with a missing or short SESSION_SECRET is refused; no password needs no secret.
def test_startup_problem(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setattr(config, "SESSION_SECRET", "short")
    assert "SESSION_SECRET" in auth.startup_problem() and "32" in auth.startup_problem()
    monkeypatch.setattr(config, "SESSION_SECRET", "")
    assert auth.startup_problem()
    monkeypatch.setattr(config, "SESSION_SECRET", SECRET)
    assert auth.startup_problem() == ""
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    monkeypatch.setattr(config, "SESSION_SECRET", "")
    assert auth.startup_problem() == ""


# ---------------------------------------------------------------- /api/auth routes

# The right password sets an httpOnly, SameSite=Lax cookie (no Secure on plain http) and opens the API.
def test_login_ok(client):
    answer = log_in(client)
    assert answer.json() == {"logged_in": True, "password_set": True}
    cookie = answer.headers["set-cookie"].lower()
    assert cookie.startswith(f"{COOKIE_NAME}=") and "httponly" in cookie and "samesite=lax" in cookie
    assert "max-age=43200" in cookie and "secure" not in cookie
    assert PASSWORD not in answer.headers["set-cookie"]               # the cookie never holds the password
    assert client.get("/api/auth/me").json() == {"logged_in": True, "password_set": True}
    assert client.get(PROTECTED).status_code == 200


# Behind https (the proxy says so) the cookie is also Secure.
def test_cookie_secure_on_https(client):
    answer = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in answer.headers["set-cookie"].lower()


# A wrong password: 401, no cookie, still logged out.
def test_wrong_password(client):
    answer = client.post("/api/auth/login", json={"password": "nope"})
    assert answer.status_code == 401 and answer.json() == {"error": "Wrong password."}
    assert "set-cookie" not in answer.headers
    assert client.get("/api/auth/me").json() == {"logged_in": False, "password_set": True}


# The 6th try within a minute is refused (429), even with the right password; another address may still try.
def test_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"password": PASSWORD})
    assert blocked.status_code == 429 and "Too many wrong passwords" in blocked.json()["error"]
    assert int(blocked.headers["Retry-After"]) >= 1
    other = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"X-Forwarded-For": "10.0.0.9"})
    assert other.status_code == 200


# Logout clears the cookie and the old token stops working too.
def test_logout(client):
    log_in(client)
    token = client.cookies.get(COOKIE_NAME)
    answer = client.post("/api/auth/logout")
    assert answer.status_code == 200 and answer.json() == {"logged_in": False, "password_set": True}
    assert f'{COOKIE_NAME}=""' in answer.headers["set-cookie"] or "max-age=0" in answer.headers["set-cookie"].lower()
    assert client.get(PROTECTED).status_code == 401
    replay = TestClient(client.app, cookies={COOKIE_NAME: token})       # someone who copied the old cookie
    assert replay.get(PROTECTED).status_code == 401


# A protected route: 401 without the cookie or with a made-up one, 200 with a real one.
def test_protected_route(client):
    answer = client.get(PROTECTED)
    assert answer.status_code == 401 and "log in" in answer.json()["error"]
    fake = TestClient(client.app, cookies={COOKIE_NAME: "9999999999.abc.forged"})
    assert fake.get(PROTECTED).status_code == 401
    log_in(client)
    assert client.get(PROTECTED).status_code == 200


# /api/health, /api/auth/me and the API docs stay open; unknown /api addresses also ask for a login first.
def test_open_addresses(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/api/no-such-thing").status_code == 401


# Without DASHBOARD_PASSWORD everything is open, as before, and login answers "no password".
def test_no_password_means_open(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    monkeypatch.setattr(config, "SESSION_SECRET", "")
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (True, "Connected"))
    c = TestClient(api_main.create_app())
    assert c.get(PROTECTED).status_code == 200
    assert c.get("/api/auth/me").json() == {"logged_in": True, "password_set": False}
    assert c.post("/api/auth/login", json={"password": "x"}).json() == {"logged_in": True, "password_set": False}


# A password with a short SESSION_SECRET: the API refuses to start, with a clear message.
def test_api_refuses_to_start(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setattr(config, "SESSION_SECRET", "too short")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        with TestClient(api_main.create_app()):            # "with" runs the startup step
            pass
    from marketlens.api.__main__ import main
    with pytest.raises(SystemExit, match="not started"):
        main(["--no-reload"])


# The webhook keeps its own WEBHOOK_SECRET check: no login cookie needed, a wrong secret is still refused.
def test_webhook_unaffected(locked, db_url, db_session, monkeypatch):
    webhook_secret = "w" * 30
    monkeypatch.setattr(config, "WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    c = TestClient(api_main.create_app(url=db_url, notify=[].append))
    message = {"secret": webhook_secret, "strategy": "EMA cross", "symbol": "RELIANCE", "action": "BUY", "qty": 10,
               "price": 2500}
    answer = c.post("/webhook/tradingview", json=message)
    assert answer.status_code == 200 and answer.json()["status"] == "ok"
    wrong = c.post("/webhook/tradingview", json={**message, "secret": "wrong"})
    assert wrong.status_code == 401 and "log in" not in str(wrong.json())


# ---------------------------------------------------------------- no Streamlit in the backend

# With Streamlit blocked, the API (every route) and the shared modules still import.
def test_api_imports_without_streamlit():
    code = ("import sys; sys.modules['streamlit'] = None\n"
            "import marketlens.api.main, marketlens.data, marketlens.auth, marketlens.costs\n"
            "import marketlens.backtest.present\n"
            "assert marketlens.api.main.find_routers()\n"
            "print('ok')")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                          cwd=str(Path(__file__).resolve().parent.parent))
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().endswith("ok")
