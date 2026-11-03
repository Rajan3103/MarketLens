"""The backend API: one FastAPI app for the future web frontend AND the webhook receiver.

    python -m marketlens.api            # starts it on http://127.0.0.1:8000 (API_PORT)
    http://127.0.0.1:8000/docs          # every endpoint, with a "Try it out" button

create_app() includes every router found in marketlens/api/routes/ (each file there that has a
`router` is added automatically), so a new group of endpoints is just a new file in that folder.

Errors: a mistake in what was asked (a bad symbol, a missing field) answers 400, anything else 500,
always as {"error": "<plain-English message>"}. The full Python error goes to logs/marketlens.log and
error_log, never to the browser.
"""
import importlib
import pkgutil
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

import marketlens
from marketlens import config
from marketlens.api import routes
from marketlens.api.errors import ApiError
from marketlens.api.schemas import Health
from marketlens.logger import get_logger, record_error

log = get_logger(__name__)


# Every router in marketlens/api/routes/, in file-name order.
def find_routers() -> list:
    found = []
    for info in sorted(pkgutil.iter_modules(routes.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{routes.__name__}.{info.name}")
        if getattr(module, "router", None) is not None:
            found.append(module.router)
    return found


# "symbol: Field required; period: ..." from FastAPI's list of input problems.
def _input_problems(err: RequestValidationError) -> str:
    parts = []
    for problem in err.errors():
        where = ".".join(str(p) for p in problem.get("loc", []) if p not in ("query", "body", "path"))
        parts.append(f"{where}: {problem.get('msg', 'not valid')}" if where else problem.get("msg", "not valid"))
    return "Please check the request: " + "; ".join(parts)


# Build the app. url / clock / notify / execute go to the webhook receiver (tests pass their own).
def create_app(url: str | None = None, clock=time.monotonic, notify=None, execute=None) -> FastAPI:
    from marketlens.webhook.server import Receiver, health_info
    app = FastAPI(title="MarketLens API", version=marketlens.__version__,
                  description="Market data, strategies and trading for the MarketLens frontend; also receives "
                              "TradingView / Chartink webhook alerts under /webhook.")
    app.state.url = url
    app.state.receiver = Receiver(url, clock, notify, execute)

    @app.middleware("http")
    async def friendly_errors(request: Request, call_next):
        """Any unexpected error becomes {"error": ...} with status 500; the details go to the log only."""
        try:
            return await call_next(request)
        except Exception as err:
            record_error(__name__, f"API {request.method} {request.url.path} failed: {type(err).__name__}: {err}",
                         {"path": request.url.path}, url=url)
            log.exception("API error on %s %s", request.method, request.url.path)
            return JSONResponse({"error": "Something went wrong on the server. The details are in "
                                          "logs/marketlens.log."}, status_code=500)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, err: ApiError):
        log.info("API %s %s: %s", request.method, request.url.path, err.message)
        return JSONResponse({"error": err.message}, status_code=err.status)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, err: ValueError):
        log.info("API %s %s refused: %s", request.method, request.url.path, err)
        return JSONResponse({"error": str(err)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def bad_input(request: Request, err: RequestValidationError):
        return JSONResponse({"error": _input_problems(err)}, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, err: HTTPException):
        message = "No such address. See /docs for the list." if err.status_code == 404 else str(err.detail)
        return JSONResponse({"error": message}, status_code=err.status_code)

    @app.get("/api/health", response_model=Health, tags=["status"])
    def health():
        """Is the API running, can it reach the database, PAPER or LIVE, DEMO data or real?"""
        from marketlens.broker.dhan_client import has_keys
        from marketlens.db import connection_status
        ok, _ = connection_status(url)
        return Health(status="ok", version=marketlens.__version__, database="ok" if ok else "fail",
                      mode=config.TRADING_MODE.upper(), demo=bool(config.DEMO_MODE and not has_keys()))

    @app.get("/health", include_in_schema=False)
    def old_health():
        """The webhook receiver's old health address (kept so nothing that used it breaks)."""
        return health_info()

    for router in find_routers():
        app.include_router(router)
    return app


app = create_app()
