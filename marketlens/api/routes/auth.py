"""Login for the web app (the password is DASHBOARD_PASSWORD in .env).

    POST /api/auth/login   {"password": "..."}  -> sets the login cookie (12 hours)
    POST /api/auth/logout                       -> clears it
    GET  /api/auth/me                           -> {"logged_in": true/false, "password_set": true/false}

The cookie holds a signed session token (marketlens/auth.py). It is httpOnly (page scripts cannot read it),
SameSite=Lax (other websites cannot send it along with their own requests) and Secure when the site is on
https. When DASHBOARD_PASSWORD is set, main.py refuses every other /api address without this cookie (401).
"""
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from marketlens import auth, config
from marketlens.logger import get_logger

router = APIRouter(prefix="/api/auth", tags=["login"])
log = get_logger(__name__)

COOKIE_NAME = "ml_session"
LOCAL_ADDRESSES = ("127.0.0.1", "::1", "localhost", "testclient")   # testclient = FastAPI's test browser


class LoginBody(BaseModel):
    """What the login form sends."""
    password: str = ""


class AuthStatus(BaseModel):
    """Is this browser logged in, and is a password needed at all?"""
    logged_in: bool
    password_set: bool


# The visitor's IP address. The API only listens on this computer, so a request from 127.0.0.1 came through
# the Next.js server (or a proxy on the server), which puts the real address in X-Forwarded-For.
def client_address(request: Request) -> str:
    direct = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if direct in LOCAL_ADDRESSES and forwarded:
        return forwarded.split(",")[0].strip() or direct
    return direct


# True when the browser reached us over https (directly, or through a proxy that says so).
def is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() == "https"


# True when this request carries a valid login cookie (or no password is set at all).
def logged_in(request: Request) -> bool:
    return not auth.password_required() or auth.session_ok(request.cookies.get(COOKIE_NAME))


# Check the password; when right, set the login cookie.
@router.post("/login", response_model=AuthStatus)
def login(body: LoginBody, request: Request, response: Response):
    """Log in with DASHBOARD_PASSWORD. At most 5 wrong passwords per minute from one address."""
    if not auth.password_required():
        return AuthStatus(logged_in=True, password_set=False)          # nothing to log in to
    who = client_address(request)
    wait = auth.LIMITER.wait_seconds(who)
    if wait > 0:
        seconds = max(int(wait + 0.999), 1)
        return JSONResponse({"error": f"Too many wrong passwords. Try again in {seconds} seconds."},
                            status_code=429, headers={"Retry-After": str(seconds)})
    if not auth.password_ok(body.password):
        auth.LIMITER.wrong(who)
        log.warning("Wrong dashboard password from %s", who)
        return JSONResponse({"error": "Wrong password."}, status_code=401)
    auth.LIMITER.reset(who)
    response.set_cookie(COOKIE_NAME, auth.make_session_token(), max_age=int(config.SESSION_HOURS * 3600),
                        httponly=True, samesite="lax", secure=is_https(request), path="/")
    log.info("Dashboard login from %s", who)
    return AuthStatus(logged_in=True, password_set=True)


# Forget the login: the token stops working and the browser deletes the cookie.
@router.post("/logout", response_model=AuthStatus)
def logout(request: Request, response: Response):
    """Log out (safe to call when not logged in)."""
    auth.forget_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=is_https(request))
    return AuthStatus(logged_in=not auth.password_required(), password_set=auth.password_required())


# Is this browser logged in? (Always open, so the web app knows whether to show the login box.)
@router.get("/me", response_model=AuthStatus)
def me(request: Request):
    """logged_in is true when the cookie is valid, or when no password is set."""
    return AuthStatus(logged_in=logged_in(request), password_set=auth.password_required())
