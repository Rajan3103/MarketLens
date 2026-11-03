"""Start the backend API: python -m marketlens.api

    python -m marketlens.api                 # http://127.0.0.1:8000, reloads when you save a .py file
    python -m marketlens.api --no-reload     # no reloading (the default on the server)
    python -m marketlens.api --port 8010

Auto-reload (restart when a file changes) is on when developing on your PC, i.e. when SERVER_STATIC_IP
is empty in .env; on the AWS server it is off. Open http://127.0.0.1:8000/docs to try every endpoint.
"""
import argparse

import uvicorn

from marketlens import config


# Read the options and run the app with uvicorn.
def main(argv: list[str] | None = None):
    developing = not config.is_set(config.SERVER_STATIC_IP)
    parser = argparse.ArgumentParser(description="Run the MarketLens backend API.")
    parser.add_argument("--port", type=int, default=config.API_PORT, help=f"default: API_PORT ({config.API_PORT})")
    parser.add_argument("--reload", dest="reload", action="store_true", default=developing,
                        help="restart when a .py file changes (default on your PC)")
    parser.add_argument("--no-reload", dest="reload", action="store_false", help="never restart by itself")
    args = parser.parse_args(argv)
    print(f"MarketLens API on http://127.0.0.1:{args.port}  (docs: /docs, health: /api/health, "
          f"auto-reload {'on' if args.reload else 'off'}, stop: Ctrl+C)")
    uvicorn.run("marketlens.api.main:app", host="127.0.0.1", port=args.port, reload=args.reload,
                reload_dirs=["marketlens"] if args.reload else None, log_level="info")


if __name__ == "__main__":
    main()
