"""Tests for marketlens/api/jobs.py and GET / POST /api/jobs (test database only).

The work functions here are tiny; threading.Event makes the background thread wait exactly where a test
needs it, so nothing depends on timing luck.
"""
import threading
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from marketlens.api import jobs


@pytest.fixture
def url(db_url, db_session, monkeypatch):
    monkeypatch.setattr(jobs, "SAVE_EVERY_SECONDS", 0)                 # save every progress report
    return db_url


# Wait (at most 10 s) until a job has finished; returns it.
def wait_done(job_id, url):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = jobs.get_job(job_id, url)
        if job["finished"]:
            return job
        time.sleep(0.02)
    pytest.fail(f"job {job_id} did not finish: {jobs.get_job(job_id, url)}")


# A finished job keeps its progress, message and a JSON-friendly result (tables, dates, NaN).
def test_job_runs_and_stores_result(url):
    def work(job, n):
        for i in range(n):
            job.progress(100 * i / n, f"step {i + 1} of {n}")
        return {"table": pd.DataFrame({"day": pd.to_datetime(["2026-11-03"]), "x": [np.nan]}), "best": np.int64(3)}
    job_id = jobs.start_job("demo", work, {"n": 3}, url=url, wait=True)
    job = jobs.get_job(job_id, url)
    assert (job["status"], job["progress"], job["message"], job["finished"]) == ("done", 100.0, "Done", True)
    assert job["result"] == {"table": [{"day": "2026-11-03T00:00:00", "x": None}], "best": 3}
    assert job["kind"] == "demo" and job["error"] is None


# Started in a background thread: start_job answers at once, the page sees progress, then the result.
def test_job_in_background_thread(url):
    go, seen_progress = threading.Event(), {}

    def work(job):
        job.progress(40, "Downloading")
        go.wait(5)
        return {"rows": 7}
    job_id = jobs.start_job("download", work, url=url)
    deadline = time.monotonic() + 5
    while jobs.get_job(job_id, url)["progress"] < 40 and time.monotonic() < deadline:
        time.sleep(0.02)
    seen_progress.update(jobs.get_job(job_id, url))
    assert seen_progress["status"] == "running" and seen_progress["progress"] == 40.0
    assert seen_progress["message"] == "Downloading" and seen_progress["result"] is None
    go.set()
    assert wait_done(job_id, url)["result"] == {"rows": 7}


# Cancel: a running job stops at its next progress report.
def test_cancel_running_job(url):
    started, resume, reached = threading.Event(), threading.Event(), []

    def work(job):
        job.progress(10, "first")
        started.set()
        resume.wait(5)
        job.progress(50, "second")                   # raises JobCancelled here
        reached.append("after")
        return {"never": True}
    job_id = jobs.start_job("sweep", work, url=url)
    assert started.wait(5)
    assert jobs.cancel_job(job_id, url)["cancel_requested"] is True
    resume.set()
    job = wait_done(job_id, url)
    assert job["status"] == "cancelled" and job["message"] == "Cancelled by you" and job["result"] is None
    assert reached == [] and job["progress"] == 10.0
    assert jobs.cancel_job(job_id, url)["status"] == "cancelled"              # cancelling twice is harmless
    assert jobs.cancel_job("no-such-job", url) is None


# Cancel before the thread started: the queued job is cancelled at once.
def test_cancel_queued_job(url, monkeypatch):
    class NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass
    monkeypatch.setattr(jobs.threading, "Thread", NoThread)
    job_id = jobs.start_job("backtest", lambda job: None, url=url)
    assert jobs.get_job(job_id, url)["status"] == "queued"
    assert jobs.cancel_job(job_id, url)["status"] == "cancelled"


# Errors: a ValueError's own words are shown; anything else gets a plain sentence and goes to error_log.
def test_failed_jobs(url):
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import ErrorLog

    def bad_input(job):
        raise ValueError("Pick at least one ETF")

    def broken(job):
        raise RuntimeError("secret detail")

    def not_json(job):
        return {"thing": object()}
    a = jobs.get_job(jobs.start_job("etf", bad_input, url=url, wait=True), url)
    assert (a["status"], a["error"]) == ("failed", "Pick at least one ETF")
    b = jobs.get_job(jobs.start_job("etf", broken, url=url, wait=True), url)
    assert b["status"] == "failed" and "secret detail" not in b["error"] and "logs/marketlens.log" in b["error"]
    c = jobs.get_job(jobs.start_job("etf", not_json, url=url, wait=True), url)
    assert c["status"] == "failed"
    with get_session(url) as session:
        logged = session.scalars(select(ErrorLog.message)).all()
    assert any("secret detail" in m for m in logged)


# Progress is kept between 0 and 100 and only saved twice a second (but always at 100).
def test_progress_clamped_and_throttled(url, monkeypatch):
    monkeypatch.setattr(jobs, "SAVE_EVERY_SECONDS", 0.5)
    job_id = jobs.start_job("x", lambda job: None, url=url, wait=True)
    now = [0.0]
    ctx = jobs.JobContext(job_id, url, clock=lambda: now[0])
    ctx.progress(150, "too much")
    assert jobs.get_job(job_id, url)["progress"] == 100.0
    ctx.progress(-5, "below")                                            # within 0.5 s: not saved
    assert jobs.get_job(job_id, url)["message"] == "too much"
    now[0] = 1.0
    ctx.progress(-5, "below")
    assert jobs.get_job(job_id, url)["progress"] == 0.0


# Jobs left running when the API stopped are marked failed when it starts again.
def test_fail_orphans(url, monkeypatch):
    class NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass
    monkeypatch.setattr(jobs.threading, "Thread", NoThread)
    job_id = jobs.start_job("download", lambda job: None, url=url)
    assert jobs.fail_orphans(url) == 1
    job = jobs.get_job(job_id, url)
    assert job["status"] == "failed" and "restarted" in job["error"]


# The two endpoints.
def test_job_routes(url):
    from marketlens.api.main import create_app
    client = TestClient(create_app(url=url))
    job_id = jobs.start_job("demo", lambda job, n: {"n": n}, {"n": 2}, url=url, wait=True)
    answer = client.get(f"/api/jobs/{job_id}")
    assert answer.status_code == 200 and answer.json()["result"] == {"n": 2}
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "done"     # already finished
    missing = client.get("/api/jobs/nope")
    assert missing.status_code == 404 and "There is no job nope" in missing.json()["error"]
    assert client.post("/api/jobs/nope/cancel").status_code == 404
