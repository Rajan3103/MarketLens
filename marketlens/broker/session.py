"""Dhan login: is today's access token valid, get a new one, and remind me at 08:45.

    python -m marketlens.broker.session check      # test the token, save the result in broker_sessions
    python -m marketlens.broker.session renew      # extend a web.dhan.co token by 24 hours (before it expires)
    python -m marketlens.broker.session totp       # new token from your PIN + the 6-digit code of your authenticator app
    python -m marketlens.broker.session consent    # new token through Dhan's login page (API key + secret, 2FA)
    python -m marketlens.broker.session paste      # paste a token you generated on web.dhan.co
    python -m marketlens.broker.session remind     # Telegram reminder if today's login is not valid (run at 08:45)
    python -m marketlens.broker.session remind --daily   # keep running; remind every weekday at 08:45

Dhan's documented ways to get an access token (https://dhanhq.co/docs/v2/authentication/, checked 02-Nov-2026):
- web.dhan.co -> My Profile -> Access DhanHQ APIs -> Access Token: valid 24 hours. Paste it ("paste").
- RenewToken (GET https://api.dhan.co/v2/RenewToken): another 24 hours for a web token that has not expired.
- API key + secret (valid 12 months, DHAN_API_KEY / DHAN_API_SECRET): generate a consent, log in on Dhan's
  page with your password and 2FA, then exchange the tokenId for an access token ("consent").
- PIN + TOTP (only if TOTP is switched on for your account): POST generateAccessToken ("totp").
Every way needs YOU (password + 2FA, or the code from your authenticator app), so MarketLens never
logs in by itself. The PIN and TOTP code are typed when asked and never saved.

Safety: the token is never written to a log or a message. A new token replaces only the
DHAN_ACCESS_TOKEN line of .env (written to a temporary file first, then swapped in).
"""
import argparse
import datetime as dt
import getpass
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from marketlens import config
from marketlens.broker.candles import IST
from marketlens.broker.dhan_broker import scrub
from marketlens.broker.dhan_client import has_keys
from marketlens.logger import get_logger

log = get_logger(__name__)

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
PROFILE_URL = "https://api.dhan.co/v2/profile"
RENEW_URL = "https://api.dhan.co/v2/RenewToken"
CONSENT_URL = "https://auth.dhan.co/app/generate-consent?client_id={client_id}"
LOGIN_URL = "https://auth.dhan.co/login/consentApp-login?consentAppId={consent_id}"
CONSUME_URL = "https://auth.dhan.co/app/consumeApp-consent?tokenId={token_id}"
TOTP_URL = "https://auth.dhan.co/app/generateAccessToken?dhanClientId={client_id}&pin={pin}&totp={totp}"
REMIND_AT = dt.time(8, 45)
MARKET_CLOSE = dt.time(15, 30)

DAILY_STEPS = """Daily Dhan login (needs you, because of 2FA):
1. Before 09:00, do ONE of these:
   a) web.dhan.co -> My Profile -> Access DhanHQ APIs -> generate an Access Token, then
      python -m marketlens.broker.session paste
   b) TOTP switched on in Dhan: python -m marketlens.broker.session totp   (PIN + authenticator code)
   c) API key + secret in .env:  python -m marketlens.broker.session consent  (log in on Dhan's page)
   d) Yesterday's web token still valid: python -m marketlens.broker.session renew
2. Check it: python -m marketlens.broker.session check   (status must be 'active')
3. Restart the app and any background process so they read the new token."""


@dataclass
class LoginStatus:
    """The result of check_login()."""
    status: str                          # active, expired or failed (broker_sessions.status)
    message: str
    expiry: dt.datetime | None = None
    checked_at: dt.datetime | None = None

    # True when the token works now and lasts until the market closes today.
    def valid_for_today(self, now: dt.datetime | None = None) -> bool:
        now = now or dt.datetime.now(IST)
        close = dt.datetime.combine(now.astimezone(IST).date(), MARKET_CLOSE, IST)
        return self.status == "active" and (self.expiry is None or self.expiry >= close)


class LoginError(RuntimeError):
    """Getting or checking a token did not work (the message never contains the token)."""


# Send one HTTP request and return the JSON answer (tests replace this function).
def http(method: str, url: str, headers: dict | None = None, timeout: float = 15) -> dict:
    request = urllib.request.Request(url, method=method, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            body = answer.read().decode() or "{}"
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace") or "{}"
        try:
            data = json.loads(body)
        except ValueError:
            data = {}
        data.setdefault("_http_status", err.code)
        return data
    except (urllib.error.URLError, TimeoutError) as err:
        raise LoginError(f"Could not reach Dhan: {scrub(err)}") from None
    try:
        return json.loads(body)
    except ValueError:
        raise LoginError("Dhan answered with something that is not JSON") from None


# Dhan's date-time text ("30/03/2025 15:37" or "2025-03-30T15:37:00") as a time in India.
def parse_expiry(text) -> dt.datetime | None:
    if not text:
        return None
    text = str(text).strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        stamp = dt.datetime.fromisoformat(text)
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=IST)
    except ValueError:
        return None


# Dhan's error text from an answer, or "" (never includes headers or the token).
def _error_of(data: dict) -> str:
    for key in ("errorMessage", "message", "remarks", "error", "errorCode", "status"):
        value = data.get(key)
        if value and value != "success":
            return scrub(value if isinstance(value, str) else json.dumps(value)[:200])
    return f"HTTP {data['_http_status']}" if "_http_status" in data else ""


# ---------------------------------------------------------------- check

# Test today's token with a light Dhan call (user profile) and save the result in broker_sessions.
def check_login(save: bool = True, url: str | None = None, now: dt.datetime | None = None) -> LoginStatus:
    now = now or dt.datetime.now(IST)
    if not has_keys():
        result = LoginStatus("failed", "Dhan key not set (DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)", None, now)
    else:
        try:
            data = http("GET", PROFILE_URL, {"access-token": config.DHAN_ACCESS_TOKEN})
            result = _status_from_profile(data, now)
        except LoginError as err:
            result = LoginStatus("failed", str(err), None, now)
    log.info("Dhan login check: %s (%s)", result.status, result.message)
    if save:
        save_session(result, url)
    return result


# Turn the profile answer into a LoginStatus.
def _status_from_profile(data: dict, now: dt.datetime) -> LoginStatus:
    if "dhanClientId" not in data:
        reason = _error_of(data) or "no profile in the answer"
        return LoginStatus("expired" if data.get("_http_status") in (401, 403) or "DH-901" in reason else "failed",
                           f"Dhan did not accept the token: {reason}", None, now)
    if str(data.get("dhanClientId")) != str(config.DHAN_CLIENT_ID):
        return LoginStatus("failed", "The token belongs to a different Dhan client ID than DHAN_CLIENT_ID", None, now)
    expiry = parse_expiry(data.get("tokenValidity"))
    if expiry is not None and expiry <= now:
        return LoginStatus("expired", f"The token expired at {expiry:%d-%b-%Y %H:%M}", expiry, now)
    until = f" until {expiry:%d-%b-%Y %H:%M}" if expiry else ""
    return LoginStatus("active", f"Token valid{until}", expiry, now)


# Save one check in broker_sessions (one row per day; a later check the same day replaces it).
def save_session(result: LoginStatus, url: str | None = None):
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import BrokerSession
    day = (result.checked_at or dt.datetime.now(IST)).astimezone(IST).date()
    try:
        with get_session(url) as session:
            row = session.scalar(select(BrokerSession).where(BrokerSession.broker == "dhan",
                                                            BrokerSession.session_date == day))
            if row is None:
                row = BrokerSession(broker="dhan", session_date=day, status=result.status)
                session.add(row)
            row.status, row.token_expiry, row.message = result.status, result.expiry, scrub(result.message)[:500]
            if result.status == "active":
                row.logged_in_at = result.checked_at
            session.commit()
    except Exception as err:                  # a database problem must not hide the login result
        log.warning("Could not save the Dhan login check: %s", scrub(err))


# ---------------------------------------------------------------- .env

# Replace (or add) one KEY=value line in .env without touching the other lines.
def set_env_value(key: str, value: str, path: Path = ENV_PATH):
    """Writes a temporary file next to .env and swaps it in, so .env is never half-written."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        raise ValueError("Bad setting name")
    if not value or re.search(r"\s", value):
        raise ValueError("The value is empty or contains spaces / line breaks")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*{key}\s*=")
    found = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write("\n".join(lines) + "\n")
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# Save a new access token in .env and in this running program.
def store_token(token: str, expiry: dt.datetime | None = None, path: Path = ENV_PATH):
    token = (token or "").strip()
    if len(token) < 20 or re.search(r"\s", token):
        raise LoginError("That does not look like a Dhan access token (too short or contains spaces)")
    set_env_value("DHAN_ACCESS_TOKEN", token, path)
    config.DHAN_ACCESS_TOKEN = token
    os.environ["DHAN_ACCESS_TOKEN"] = token
    log.info("New Dhan access token saved in .env%s", f" (valid until {expiry:%d-%b-%Y %H:%M})" if expiry else "")


# ---------------------------------------------------------------- the ways to get a token

# Extend a web.dhan.co token by 24 hours (it must still be valid); returns (token, expiry).
def renew_web_token() -> tuple[str, dt.datetime | None]:
    if not has_keys():
        raise LoginError("Dhan key not set (DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN): nothing to renew")
    data = http("GET", RENEW_URL, {"access-token": config.DHAN_ACCESS_TOKEN, "dhanClientId": config.DHAN_CLIENT_ID})
    token = data.get("accessToken") or data.get("token")
    if not token:
        raise LoginError(f"Dhan did not renew the token: {_error_of(data) or 'no token in the answer'}. "
                         "An expired token cannot be renewed: generate a new one.")
    return token, parse_expiry(data.get("expiryTime"))


# A new token from your PIN and the 6-digit code of your authenticator app (TOTP on).
def token_from_totp(pin: str, totp: str) -> tuple[str, dt.datetime | None]:
    if not config.is_set(config.DHAN_CLIENT_ID):
        raise LoginError("DHAN_CLIENT_ID is not set in .env")
    if not re.fullmatch(r"\d{4,6}", pin or ""):
        raise LoginError("The PIN must be 4 to 6 digits")
    if not re.fullmatch(r"\d{6}", totp or ""):
        raise LoginError("The TOTP code must be the 6 digits shown in your authenticator app")
    data = http("POST", TOTP_URL.format(client_id=urllib.parse.quote(config.DHAN_CLIENT_ID),
                                        pin=urllib.parse.quote(pin), totp=urllib.parse.quote(totp)))
    token = data.get("accessToken")
    if not token:
        raise LoginError(f"Dhan did not give a token: {_error_of(data) or 'no token in the answer'}")
    return token, parse_expiry(data.get("expiryTime"))


# Step 1 of the API key + secret way: returns (consentAppId, the login page to open in your browser).
def consent_start() -> tuple[str, str]:
    if not (config.is_set(config.DHAN_API_KEY) and config.is_set(config.DHAN_API_SECRET)
            and config.is_set(config.DHAN_CLIENT_ID)):
        raise LoginError("Set DHAN_CLIENT_ID, DHAN_API_KEY and DHAN_API_SECRET in .env first "
                         "(web.dhan.co -> My Profile -> Access DhanHQ APIs -> API key)")
    data = http("POST", CONSENT_URL.format(client_id=urllib.parse.quote(config.DHAN_CLIENT_ID)),
                {"app_id": config.DHAN_API_KEY, "app_secret": config.DHAN_API_SECRET})
    consent_id = data.get("consentAppId")
    if not consent_id:
        raise LoginError(f"Dhan did not start the login: {_error_of(data) or 'no consentAppId'}")
    return consent_id, LOGIN_URL.format(consent_id=urllib.parse.quote(str(consent_id)))


# Step 3: swap the tokenId from the redirect address (after you logged in) for an access token.
def consent_finish(token_id: str) -> tuple[str, dt.datetime | None]:
    token_id = (token_id or "").strip()
    if "tokenId=" in token_id:                      # the whole redirect address was pasted
        token_id = urllib.parse.parse_qs(urllib.parse.urlparse(token_id).query).get("tokenId", [""])[0]
    if not token_id:
        raise LoginError("No tokenId: copy it from the address bar after logging in on Dhan's page")
    data = http("GET", CONSUME_URL.format(token_id=urllib.parse.quote(token_id)),
                {"app_id": config.DHAN_API_KEY, "app_secret": config.DHAN_API_SECRET})
    token = data.get("accessToken")
    if not token:
        raise LoginError(f"Dhan did not give a token: {_error_of(data) or 'no token in the answer'}")
    if data.get("dhanClientId") and str(data["dhanClientId"]) != str(config.DHAN_CLIENT_ID):
        raise LoginError("The token is for a different Dhan client ID than DHAN_CLIENT_ID: not saved")
    return token, parse_expiry(data.get("expiryTime"))


# Get a new token one of the four ways, save it in .env, and check it. Returns the check result.
def renew_token(method: str = "renew", path: Path = ENV_PATH, url: str | None = None, **kw) -> LoginStatus:
    """method: renew, totp (pin=, totp=), consent (token_id= after consent_start) or paste (token=)."""
    if method == "renew":
        token, expiry = renew_web_token()
    elif method == "totp":
        token, expiry = token_from_totp(kw.get("pin", ""), kw.get("totp", ""))
    elif method == "consent":
        token, expiry = consent_finish(kw.get("token_id", ""))
    elif method == "paste":
        token, expiry = kw.get("token", ""), None
    else:
        raise ValueError("method must be renew, totp, consent or paste")
    old = config.DHAN_ACCESS_TOKEN
    config.DHAN_ACCESS_TOKEN = token.strip()          # test the new token before saving it
    result = check_login(save=True, url=url)
    if result.status == "failed" and method == "renew":
        # RenewToken has already ended the old token, so keep the new one even if the check could not run
        store_token(token, expiry, path)
        raise LoginError(f"The token was renewed and saved, but could not be checked ({result.message}). "
                         "Run `python -m marketlens.broker.session check` again in a minute.")
    if result.status != "active":
        config.DHAN_ACCESS_TOKEN = old
        raise LoginError(f"The new token did not work ({result.message}): .env was not changed")
    store_token(token, result.expiry or expiry, path)
    return result


# ---------------------------------------------------------------- 08:45 reminder

# The Telegram text when today's login is not valid.
def reminder_text(result: LoginStatus) -> str:
    return (f"⚠️ MarketLens: Dhan login is not ready for today ({result.status}: {result.message}).\n"
            "Live orders are blocked until you log in.\n\n" + DAILY_STEPS)


# On a weekday, check the login and send a Telegram reminder if it will not last the trading day.
def remind_if_needed(now: dt.datetime | None = None, send=None, url: str | None = None) -> str | None:
    """Returns the message sent, or None (weekend, or the login is fine)."""
    now = now or dt.datetime.now(IST)
    if now.astimezone(IST).weekday() >= 5:
        return None
    result = check_login(save=True, url=url, now=now)
    if result.valid_for_today(now):
        return None
    if result.status == "active":
        result = LoginStatus("active", f"{result.message}, but it expires before the market closes",
                             result.expiry, result.checked_at)
    text = reminder_text(result)
    if send is None:
        from marketlens.alerts.telegram import notify
        notify(text, source="login", event="login_reminder", url=url)
    else:
        send(text)
    return text


# Seconds until the next weekday 08:45 in India.
def seconds_until_next(now: dt.datetime | None = None, at: dt.time = REMIND_AT) -> float:
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    target = dt.datetime.combine(now.date(), at, IST)
    if target <= now:
        target += dt.timedelta(days=1)
    while target.weekday() >= 5:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


# ---------------------------------------------------------------- command line

# The commands listed at the top of this file.
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m marketlens.broker.session", description="Dhan login helper")
    parser.add_argument("command", choices=["check", "renew", "totp", "consent", "paste", "remind", "steps"])
    parser.add_argument("--daily", action="store_true", help="remind: keep running, every weekday at 08:45")
    args = parser.parse_args(argv)
    try:
        if args.command == "steps":
            print(DAILY_STEPS)
        elif args.command == "check":
            result = check_login()
            print(f"{result.status}: {result.message}")
            return 0 if result.status == "active" else 1
        elif args.command == "renew":
            print(f"active: {renew_token('renew').message}")
        elif args.command == "totp":
            pin = getpass.getpass("Dhan PIN (not shown, not saved): ")
            code = input("6-digit code from your authenticator app: ").strip()
            print(f"active: {renew_token('totp', pin=pin, totp=code).message}")
        elif args.command == "consent":
            consent_id, login_url = consent_start()
            print(f"1. Open this page, log in with your password and 2FA:\n   {login_url}\n"
                  "2. After the login, your browser opens your Redirect URL with ?tokenId=... at the end.")
            token_id = input("3. Paste that tokenId (or the whole address): ").strip()
            print(f"active: {renew_token('consent', token_id=token_id).message}")
        elif args.command == "paste":
            token = getpass.getpass("Paste the access token from web.dhan.co (not shown): ")
            print(f"active: {renew_token('paste', token=token).message}")
        elif args.command == "remind":
            while True:
                sent = remind_if_needed()
                print("Reminder sent on Telegram." if sent else "Login is fine (or it is the weekend): no reminder.")
                if not args.daily:
                    break
                time.sleep(seconds_until_next())
    except (LoginError, ValueError) as err:
        print(f"Error: {scrub(err)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
