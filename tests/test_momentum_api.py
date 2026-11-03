"""TestClient tests for marketlens/api/routes/momentum.py (module 8).

The first tests need no database: jobs run at once with a stand-in, prices are made up. The last ones use
TEST_DATABASE_URL with real background jobs (skipped when it is not set). Nothing touches the real database.
"""
import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

from marketlens import config
from marketlens.api import main as api_main
from marketlens.api.routes import momentum
from marketlens.momentum import portfolio_bt as pb
from tests.test_momentum_portfolio import stored  # noqa: F401  (a fixture)

QUICK = {"universe": "nifty50", "start": "2019-01-01", "end": "2021-12-31", "top_n": 5, "hold_rank": 8,
         "tax": {"enabled": False},
         "filters": {"lookbacks": [63], "weights": [1], "skip_recent": 0, "ma_days": 0, "index_filter": False}}


class FakeJob:
    """Stands in for JobContext: remembers progress."""
    def __init__(self):
        self.steps = []

    def progress(self, percent, message=None):
        self.steps.append((percent, message))


@pytest.fixture
def client(monkeypatch):
    """The API without a database: start_job runs the work at once and keeps the answer."""
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (True, "ok"))
    monkeypatch.setattr(api_main, "record_error", lambda *a, **k: None)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/never")   # never reached
    finished = {}

    def start_job(kind, work, params=None, url=None):
        job = FakeJob()
        finished[kind] = {"result": work(job, **(params or {})), "steps": job.steps, "params": params}
        return f"{kind}-1"
    monkeypatch.setattr(momentum, "start_job", start_job)
    monkeypatch.setattr(pb, "load_stock_data", lambda s, url=None, allow_missing=False: pb.demo_stock_data(
        n=20, start=dt.date(2017, 6, 1), end=dt.date(2021, 12, 31)))
    c = TestClient(api_main.create_app())
    c.finished = finished
    return c


# The form's defaults and choices.
def test_options(client):
    body = client.get("/api/momentum/options").json()
    assert body["defaults"]["universe"] == "nifty200" and body["defaults"]["filters"]["lookbacks"] == [63, 126, 252]
    assert [u["id"] for u in body["universes"]] == ["nifty50", "nifty200"] and "200 stocks" in body["universes"][1]["label"]
    assert body["weightings"] == ["equal", "inverse_vol"] and "not a live trading recommendation" in body["note"]


# A backtest starts a job; its result is the whole report (cards, comparison, charts, tables).
def test_backtest_job_result(client):
    answer = client.post("/api/momentum/backtest", json={"settings": QUICK, "save": False})
    assert answer.status_code == 200 and answer.json() == {"job_id": "momentum_backtest-1"}
    job = client.finished["momentum_backtest"]
    assert "url" not in job["params"]                              # the database address is never saved
    r = job["result"]
    assert r["is_demo"] is True and r["run_id"] is None and r["start"] >= "2019-01-01"
    assert r["summary"]["final_value"] > 0 and r["summary"]["rebalance_count"] == len(r["rebalances"]) > 30
    assert [row["Measure"] for row in r["comparison"]][:3] == ["Final value (Rs)", "Total return %", "CAGR %"]
    assert pb.NIFTY_NAME in r["comparison"][0]
    names = [t["name"] for t in r["equity_chart"]["data"]]
    assert names[0] == "Strategy" and any("Nifty 50" in n for n in names)
    assert r["holdings"][-1]["symbol"] == "Cash" and r["trades"] and r["yearly"][0]["Year"] == 2019
    assert any("survivorship" in n for n in r["notes"])
    assert job["steps"][-1][0] >= 95 and "Rebalance" in job["steps"][-2][1]


# Bad settings are refused at once with a friendly 400 (no job started).
def test_backtest_bad_settings(client):
    bad = client.post("/api/momentum/backtest", json={"settings": {**QUICK, "hold_rank": 2}})
    assert bad.status_code == 400 and "top M" in bad.json()["error"]
    unknown = client.post("/api/momentum/backtest", json={"settings": {**QUICK, "filters": {"speed": 1}}})
    assert unknown.status_code == 400 and "Unknown setting" in unknown.json()["error"]
    assert "momentum_backtest" not in client.finished


# Downloading prices is a job too, with the settings' dates.
def test_sync_prices_job(client, monkeypatch):
    asked = {}
    monkeypatch.setattr(pb, "sync_universe_prices", lambda s, progress=None, url=None: asked.update(
        universe=s.universe, start=s.history_start()) or {"rows": 10, "failed": [], "up_to_date": 0})
    answer = client.post("/api/momentum/sync-prices", json={"settings": QUICK})
    assert answer.json()["job_id"] == "momentum_sync_prices-1"
    assert asked["universe"] == "nifty50" and asked["start"] < dt.date(2019, 1, 1)
    assert client.finished["momentum_sync_prices"]["result"]["rows"] == 10


# Every route is listed in the API docs.
def test_routes_listed(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {"/api/momentum/backtest", "/api/momentum/ranks", "/api/momentum/runs/{run_id}/rebalances",
            "/api/momentum/sync-prices", "/api/momentum/strategies", "/api/momentum/data-check",
            "/api/momentum/strategies/{strategy_id}", "/api/momentum/runs", "/api/momentum/options"} <= paths


# ---------------------------------------------------------------- with the test database

# Ask a job until it has finished (real background thread).
def wait_for(c: TestClient, job_id: str, seconds: float = 60) -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["finished"]:
            return job
        time.sleep(0.2)
    raise AssertionError("the job did not finish")


@pytest.fixture
def db_client(stored, monkeypatch):
    monkeypatch.setattr(api_main, "record_error", lambda *a, **k: None)
    return TestClient(api_main.create_app(url=stored))


# Missing prices are listed (and nothing is missing for the stored period).
def test_data_check(db_client):
    ok = db_client.post("/api/momentum/data-check", json={"settings": {**QUICK, "top_n": 2, "hold_rank": 3}}).json()
    assert ok["ok"] is True and ok["problems"] == [] and ok["warning"] is None
    late = db_client.post("/api/momentum/data-check",
                          json={"settings": {**QUICK, "top_n": 2, "hold_rank": 3, "end": "2022-06-30"}}).json()
    assert late["ok"] is False and len(late["problems"]) == 6 and "sync_prices()" in late["message"]


# The whole path: backtest job -> saved run -> rebalance history -> save strategy -> open it -> ranks.
def test_backtest_save_and_history(db_client):
    s = {**QUICK, "top_n": 2, "hold_rank": 3}
    job = wait_for(db_client, db_client.post("/api/momentum/backtest", json={"settings": s}).json()["job_id"])
    assert job["status"] == "done", job["error"]
    run_id = job["result"]["run_id"]
    assert run_id and job["result"]["is_demo"] is False
    assert db_client.get("/api/momentum/runs").json()["runs"][0]["run_id"] == run_id

    history = db_client.get(f"/api/momentum/runs/{run_id}/rebalances").json()["rebalances"]
    assert history[0]["date"] >= "2019-01-01" and history[0]["buys"] and history[0]["sells"] == []
    assert sum(len(h["sells"]) for h in history) > 0
    assert db_client.get("/api/momentum/runs/9999/rebalances").status_code == 404

    saved = db_client.post("/api/momentum/strategies", json={"name": "Web momentum", "settings": s}).json()
    opened = db_client.get(f"/api/momentum/strategies/{saved['strategy_id']}").json()
    assert opened["name"] == "Web momentum" and opened["settings"]["top_n"] == 2
    assert db_client.get("/api/momentum/strategies/9999").status_code == 404

    ranks = db_client.get("/api/momentum/ranks", params={"strategy_id": saved["strategy_id"],
                                                         "date": "2021-12-31"}).json()
    assert ranks["as_of"] == "2021-12-31" and len(ranks["rows"]) == 5 and "not advice" in ranks["note"]
    assert sum(r["in_top_n"] for r in ranks["rows"]) == 2 and ranks["rows"][0]["rank"] == 1
    import json
    own = db_client.get("/api/momentum/ranks", params={"settings": json.dumps({**s, "top_n": 3}),
                                                      "date": "2021-12-31"}).json()
    assert own["top_n"] == 3 and sum(r["in_top_n"] for r in own["rows"]) == 3
    assert db_client.get("/api/momentum/ranks", params={"settings": "{oops"}).status_code == 400


# A backtest with missing prices fails with the list of what is missing (shown by the progress bar).
def test_backtest_job_missing_prices(db_client):
    job = wait_for(db_client, db_client.post("/api/momentum/backtest", json={
        "settings": {**QUICK, "top_n": 2, "hold_rank": 3, "end": "2022-06-30"}}).json()["job_id"])
    assert job["status"] == "failed" and "Prices are missing for 6 symbol(s)" in job["error"]
