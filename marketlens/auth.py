"""The dashboard login, shared by the Streamlit app and the web API. No Streamlit here.

    password_ok(typed)          is the typed password DASHBOARD_PASSWORD? (constant-time compare)
    LIMITER                     at most 5 wrong passwords per minute from one address (IP)
    make_session_token()        a signed "logged in" ticket, valid for 12 hours
    session_ok(token)           is this ticket genuine, not expired and not logged out?
    forget_session(token)       logout: the ticket stops working at once

A session token is plain text with three parts: "<expires>.<random id>.<signature>". The signature is an
HMAC-SHA256 of the first two parts made with SESSION_SECRET (and the current password), so nobody can make a
valid token or change its expiry time without the secret. Changing DASHBOARD_PASSWORD or SESSION_SECRET in
.env logs everyone out.
"""
import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque

from marketlens import config

MAX_WRONG = 5                    # wrong passwords allowed ...
WINDOW_SECONDS = 60              # ... per minute, per address


# True when a password lock is switched on (DASHBOARD_PASSWORD is filled in).
def password_required() -> bool:
    return config.is_set(config.DASHBOARD_PASSWORD)


# True when the typed password matches DASHBOARD_PASSWORD (compared in constant time, so the time taken
# does not give away how many letters were right).
def password_ok(typed: str | None, expected: str | None = None) -> bool:
    expected = config.DASHBOARD_PASSWORD if expected is None else expected
    return hmac.compare_digest((typed or "").encode("utf-8"), (expected or "").encode("utf-8"))


class LoginLimiter:
    """Counts wrong passwords per address and says when to pause.

        seconds = LIMITER.wait_seconds(ip)      # 0 = may try now
        LIMITER.wrong(ip)                       # after a wrong password
        LIMITER.reset(ip)                       # after the right one

    Safe to share between threads. clock can be swapped for a fake one in tests.
    """

    # max_wrong wrong tries are allowed in any `window` seconds.
    def __init__(self, max_wrong: int = MAX_WRONG, window: float = WINDOW_SECONDS, clock=time.monotonic):
        self.max_wrong, self.window, self._clock = max_wrong, window, clock
        self._wrong: dict[str, deque] = {}        # address -> times of recent wrong tries
        self._lock = threading.Lock()

    # Drop wrong tries older than the window, and addresses with none left (call with the lock held).
    def _trim(self, key: str, now: float) -> deque:
        times = self._wrong.get(key, deque())
        while times and now - times[0] >= self.window:
            times.popleft()
        if times:
            self._wrong[key] = times
        else:
            self._wrong.pop(key, None)
        return times

    # Seconds until this address may try again (0 = now).
    def wait_seconds(self, key: str) -> float:
        with self._lock:
            now = self._clock()
            times = self._trim(key, now)
            if len(times) < self.max_wrong:
                return 0.0
            return max(self.window - (now - times[0]), 0.0)

    # Remember one wrong password from this address.
    def wrong(self, key: str):
        with self._lock:
            now = self._clock()
            times = self._trim(key, now)
            times.append(now)
            self._wrong[key] = times

    # The right password: forget this address's wrong tries.
    def reset(self, key: str):
        with self._lock:
            self._wrong.pop(key, None)

    # Forget everything (tests use this).
    def clear(self):
        with self._lock:
            self._wrong.clear()


LIMITER = LoginLimiter()


# ---------------------------------------------------------------- session tokens

_logged_out: dict[str, float] = {}           # random id -> expiry time, for tokens ended by Logout
_logged_out_lock = threading.Lock()


# The signing key: SESSION_SECRET plus the password, so changing either one ends every login.
def _key() -> bytes:
    return f"{config.SESSION_SECRET}|{config.DASHBOARD_PASSWORD}".encode("utf-8")


# The signature of "<expires>.<id>" as URL-safe text.
def _sign(body: str) -> str:
    digest = hmac.new(_key(), body.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# A new signed token, valid for SESSION_HOURS from `now` (seconds since 1970).
def make_session_token(now: float | None = None, hours: float | None = None) -> str:
    now = time.time() if now is None else now
    expires = int(now + (config.SESSION_HOURS if hours is None else hours) * 3600)
    body = f"{expires}.{secrets.token_urlsafe(16)}"
    return f"{body}.{_sign(body)}"


# The (expires, id) inside a token when its signature is right, else None.
def _read(token: str | None):
    parts = (token or "").split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        return None
    body = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(_sign(body), parts[2]):
        return None
    return int(parts[0]), parts[1]


# True when the token is genuine, not expired and not logged out.
def session_ok(token: str | None, now: float | None = None) -> bool:
    if not config.is_set(config.SESSION_SECRET):
        return False                              # nothing to check a signature with
    found = _read(token)
    if found is None:
        return False
    expires, token_id = found
    now = time.time() if now is None else now
    if now >= expires:
        return False
    with _logged_out_lock:
        return token_id not in _logged_out


# Logout: this token stops working at once (remembered until it would have expired anyway).
def forget_session(token: str | None, now: float | None = None):
    found = _read(token)
    if found is None:
        return
    now = time.time() if now is None else now
    with _logged_out_lock:
        for old in [i for i, until in _logged_out.items() if until <= now]:   # tidy up expired ones
            del _logged_out[old]
        _logged_out[found[1]] = found[0]


# Why the API must not start, or "" when the login settings are fine.
def startup_problem() -> str:
    if password_required() and not config.session_secret_strong():
        return (f"DASHBOARD_PASSWORD is set, so SESSION_SECRET in .env must be at least "
                f"{config.SESSION_SECRET_MIN_LENGTH} characters (it signs the login cookie). Make one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"')
    return ""
