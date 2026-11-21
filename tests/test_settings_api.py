"""/api/settings/*, /api/scheduler/* and /api/home (routes/settings.py, settings_info.py, home.py).

No internet. Tests marked with db_url use TEST_DATABASE_URL only; the others pretend the database is down,
so nothing ever touches the real database (not even a failed login on it).
"""
import datetime as dt
from functools import partial

import pytest
from fastapi.testclient import TestClient

from marketlens import config, home, settings_info
from marketlens.api import main as api_main
from marketlens.api.jobs import start_job
from marketlens.api.routes import settings as settings_routes
from marketlens.broker.candles import IST
from marketlens.db import explain_db_error
from marketlens.scheduler import jobs as sched

# Made-up secrets: none of these may ever appear in an answer.
FAKE_SECRETS = {
    "DHAN_CLIENT_ID": "1100fakeclient77", "DHAN_ACCESS_TOKEN": "eyFakeDhanAccessToken.abc.def",
    "DHAN_API_KEY": "fake-dhan-api-key-5521", "DHAN_API_SECRET": "fake-dhan-api-secret-9931",
    "TELEGRAM_BOT_TOKEN": "7012345678:AAFakeTelegramBotToken", "TELEGRAM_CHAT_ID": "5550001112",
    "WEBHOOK_SECRET": "fake-webhook-secret-with-30-chars", "DASHBOARD_PASSWORD": "Fake Dashboard Pass!",
    "SESSION_SECRET": "fake-session-secret-" + "z" * 30,
}
DB_PASSWORD, TEST_DB_PASSWORD = "FakeDbPass987", "FakeTestPass654"


# Pretend the database is down: no connection to any database is ever tried.
def database_down(monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionError("database down (test)")
    monkeypatch.setattr("marketlens.db.get_engine", refuse)
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (False, "not running (test)"))


# The API with no keys, no password and the database down.
@pytest.fixture
def client(monkeypatch):
    for name in FAKE_SECRETS:
        monkeypatch.setattr(config, name, "")
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "AUTO_TRADE", False)
    monkeypatch.setattr(config, "TEST_DATABASE_URL", "")     # (a key in the list too; blank = "every key missing")
    database_down(monkeypatch)
    return TestClient(api_main.create_app())


# The API on the test database (no keys, no password).
@pytest.fixture
def db_client(db_url, db_session, monkeypatch):
    for name in FAKE_SECRETS:
        monkeypatch.setattr(config, name, "")
    c = TestClient(api_main.create_app(url=db_url))
    c.db = db_session
    return c


# ---------------------------------------------------------------- settings_info.py (no API)

# The page's warnings follow the settings.
def test_page_messages(monkeypatch):
    for name in FAKE_SECRETS:
        monkeypatch.setattr(config, name, "")
    monkeypatch.setattr(config, "TEST_DATABASE_URL", "")
    monkeypatch.setattr(config, "AUTO_TRADE", True)
    texts = [m["text"] for m in settings_info.page_messages()]
    assert texts[0].startswith("DASHBOARD_PASSWORD is empty") and any(t.startswith("AUTO_TRADE is on") for t in texts)
    assert any(t.startswith(f"{len(config.KEYS)} key(s) not set") for t in texts)
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "short")
    assert any(t.startswith("WEBHOOK_SECRET is shorter") for t in (m["text"] for m in settings_info.page_messages()))
    for name, value in FAKE_SECRETS.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "TEST_DATABASE_URL", "postgresql+psycopg://x:y@localhost/t")
    monkeypatch.setattr(config, "AUTO_TRADE", False)
    assert settings_info.page_messages() == []                       # everything set: nothing to warn about


# PAPER is green, LIVE red.
def test_trading_mode_message(monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    assert settings_info.trading_mode_message()[0] == "success"
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    assert settings_info.trading_mode_message() == ("error", "Mode: LIVE. Orders use real money.")


# The newest of the kept backups; other databases' dumps are not counted.
def test_backup_info(tmp_path):
    for name in ("marketlens_2026-11-08_1000.dump", "marketlens_2026-11-15_1000.dump", "other_2026-11-16_1000.dump"):
        (tmp_path / name).write_bytes(b"x" * 2_000_000)
    info = settings_info.backup_info(tmp_path, "marketlens")
    assert (info["count"], info["keep"], info["last_file"], info["size_mb"]) == (
        2, 8, "marketlens_2026-11-15_1000.dump", 2.0)
    assert info["last_time"].endswith("+05:30") and info["schedule"] == "Sunday 10:00"
    empty = settings_info.backup_info(tmp_path / "none", "marketlens")
    assert empty["count"] == 0 and empty["last_file"] is None


# The Home page rows: 12 modules, each with its group filled in.
def test_home_rows():
    rows = home.module_rows()
    assert [r["number"] for r in rows] == list(range(1, 13))
    assert rows[1]["group"] == "Market & data" and rows[11]["group"] == "Control centre"


# ---------------------------------------------------------------- routes, database down, no keys

# The status: keys (set or not), PAPER, the database problem with its hint, and the warnings.
def test_status_no_keys(client):
    body = client.get("/api/settings/status").json()
    assert body["missing_keys"] == len(config.KEYS) and not any(k["set"] for k in body["keys"])
    assert body["trading_mode"] == "PAPER" and body["trading_mode_message"]["level"] == "success"
    assert body["database"] == {"status": "fail", "message": "not running (test)",
                                "hint": settings_info.DB_HINT}
    assert body["demo"] is True and body["intro"].startswith("Keys are read from the .env file")
    assert body["messages"][0]["text"].startswith("DASHBOARD_PASSWORD is empty")


# Other settings: the same names as the Streamlit page, the database password hidden.
def test_other_settings(client, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"postgresql+psycopg://marketlens:{DB_PASSWORD}@db.local:5432/ml")
    answer = client.get("/api/settings/other")
    names = [s["name"] for s in answer.json()["settings"]]
    assert names == ["TRADING_MODE", "PAPER_CAPITAL", "AUTO_TRADE", "MAX_DAILY_LOSS_RS", "MAX_ORDERS_PER_SECOND",
                     "MAX_OPEN_POSITIONS", "DEMO_MODE", "DATA_SOURCE", "DOWNLOAD_DIR", "API_PORT", "DATABASE_URL",
                     "TEST_DATABASE_URL", "SERVER_STATIC_IP"]
    values = {s["name"]: s["value"] for s in answer.json()["settings"]}
    assert values["DATABASE_URL"] == "postgresql+psycopg://***@db.local:5432/ml" and DB_PASSWORD not in answer.text


# "Check again" asks the database again.
def test_db_check(client, monkeypatch):
    assert client.post("/api/settings/db-check").json()["status"] == "fail"
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (True, "Connected to PostgreSQL 18"))
    assert client.post("/api/settings/db-check").json() == {"status": "ok", "message": "Connected to PostgreSQL 18",
                                                             "hint": None}


# Backups route.
def test_backups_route(client, monkeypatch, tmp_path):
    (tmp_path / "marketlens_2026-11-15_1000.dump").write_bytes(b"dump")
    monkeypatch.setattr(sched, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+psycopg://marketlens@localhost:5432/marketlens")
    body = client.get("/api/settings/backups").json()
    assert body["count"] == 1 and body["last_file"] == "marketlens_2026-11-15_1000.dump" and body["keep"] == 8


# Without a database the jobs, their schedule and next runs are still listed; last runs are "unknown".
def test_scheduler_jobs_without_database(client):
    body = client.get("/api/scheduler/jobs").json()
    assert body["database"] is False and body["scheduler"]["state"] == "unknown"
    assert [j["id"] for j in body["jobs"]] == [j.id for j in sched.JOBS]
    assert all(j["status"] == "unknown" and j["next_run"].endswith("+05:30") for j in body["jobs"])
    assert body["holidays"]["list_ends"] == "2026-12-25" and "NSE holidays" in body["holidays"]["note"]


# Run now: an unknown job is a 404, and without a database a friendly 503 (never a 500).
def test_run_job_errors(client):
    unknown = client.post("/api/scheduler/jobs/nope/run")
    assert unknown.status_code == 404 and "No job called 'nope'" in unknown.json()["error"]
    no_db = client.post("/api/scheduler/jobs/eod_report/run")
    assert no_db.status_code == 503 and "needs the database" in no_db.json()["error"]


# Home: title, the 12 modules and the historical note.
def test_home_route(client):
    body = client.get("/api/home").json()
    assert body["title"] == "MarketLens" and len(body["modules"]) == 12 and body["historical"] is True
    assert body["modules"][0] == {"group": "Market & data", "number": 1, "name": "Live Market Overview",
                                  "what": home.MODULES[0][3], "status": "Ready"}


# ---------------------------------------------------------------- routes on the test database

# Last run, status and result come from scheduler_runs; a fresh heartbeat means "alive".
def test_scheduler_jobs_with_runs(db_client):
    from marketlens.models import SchedulerRun, ServiceHeartbeat
    now = dt.datetime.now(IST)
    db_client.db.add(SchedulerRun(job_id="eod_report", started_at=now - dt.timedelta(hours=1),
                                  finished_at=now - dt.timedelta(minutes=59), status="ok", message="sent (0.4 s)"))
    db_client.db.add(ServiceHeartbeat(name=sched.HEARTBEAT, pid=1, beat_at=now, message="ok"))
    db_client.db.commit()
    body = db_client.get("/api/scheduler/jobs").json()
    assert body["database"] is True and body["scheduler"]["state"] == "alive"
    eod = next(j for j in body["jobs"] if j["id"] == "eod_report")
    assert (eod["status"], eod["result"], eod["when"]) == ("ok", "sent (0.4 s)", "15:50 Mon-Fri")
    assert eod["last_run"].endswith("+05:30")
    assert next(j for j in body["jobs"] if j["id"] == "backup")["status"] == "never run"


# Run now: a background job (followed at /api/jobs/{id}) that runs the job and records the run.
def test_run_job_now(db_client, monkeypatch):
    monkeypatch.setattr(sched.BY_ID["eod_report"], "func", lambda now, url: "report sent")
    monkeypatch.setattr(settings_routes, "start_job", partial(start_job, wait=True))   # run it in this thread
    answer = db_client.post("/api/scheduler/jobs/eod_report/run")
    assert answer.status_code == 200
    started = answer.json()
    assert started["job"] == "eod_report" and started["name"] == sched.BY_ID["eod_report"].name
    job = db_client.get(f"/api/jobs/{started['job_id']}").json()
    assert job["status"] == "done" and job["result"]["status"] == "ok"
    assert job["result"]["result"].startswith("report sent")
    eod = next(j for j in db_client.get("/api/scheduler/jobs").json()["jobs"] if j["id"] == "eod_report")
    assert eod["status"] == "ok" and eod["result"].startswith("report sent")


# ---------------------------------------------------------------- no secret ever leaves the API

# Every route under /api/settings and /api/scheduler (and /api/home, /api/health), with made-up secrets filled
# in: none of the secret values, nor a database password, may appear in any answer.
def test_no_secret_in_any_answer(monkeypatch):
    for name, value in FAKE_SECRETS.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "DATABASE_URL", f"postgresql+psycopg://marketlens:{DB_PASSWORD}@localhost:5432/ml")
    monkeypatch.setattr(config, "TEST_DATABASE_URL",
                        f"postgresql+psycopg://marketlens:{TEST_DB_PASSWORD}@localhost:5432/ml_test")
    database_down(monkeypatch)
    # the real "wrong password" message, built from the address with the password in it
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (False, explain_db_error(
        'FATAL: password authentication failed for user "marketlens"', url or config.DATABASE_URL)))
    monkeypatch.setattr(settings_routes, "start_job", lambda *args, **kwargs: "fake-job-id")
    client = TestClient(api_main.create_app())
    assert client.post("/api/auth/login", json={"password": FAKE_SECRETS["DASHBOARD_PASSWORD"]}).status_code == 200

    secrets = list(FAKE_SECRETS.values()) + [DB_PASSWORD, TEST_DB_PASSWORD]
    walked = []
    for path, methods in client.get("/openapi.json").json()["paths"].items():       # every route the app has
        if not path.startswith(("/api/settings", "/api/scheduler", "/api/home", "/api/health")):
            continue
        path = path.replace("{name}", "eod_report")
        for method in (m.upper() for m in methods):
            answer = client.request(method, path)
            walked.append(f"{method} {path}")
            assert answer.status_code < 500, (method, path, answer.text)
            leaked = [s for s in secrets if s in answer.text or s in str(answer.headers)]
            assert not leaked, f"{method} {path} shows a secret"
    assert len(walked) >= 8, walked                       # every route was really called
    assert {"GET /api/settings/status", "GET /api/settings/other", "POST /api/settings/db-check",
            "GET /api/settings/backups", "GET /api/scheduler/jobs",
            "POST /api/scheduler/jobs/eod_report/run"} <= set(walked)


# The cost boxes: the same list as costs.COST_FIELDS, so the web form matches Streamlit's.
def test_cost_fields_route(client):
    from marketlens.costs import COST_FIELDS
    fields = client.get("/api/settings/cost-fields").json()["fields"]
    assert fields == COST_FIELDS and [f["name"] for f in fields][0] == "brokerage_per_order"
