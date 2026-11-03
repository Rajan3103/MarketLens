"""Background jobs: slow work (downloads, backtests, sweeps) runs in a thread while the page shows progress.

    # in a route:
    def sweep(job, symbols):                      # the work: gets a JobContext first
        for i, s in enumerate(symbols):
            job.progress(100 * i / len(symbols), f"Testing {s}")   # also stops here if you pressed Cancel
            ...
        return {"best": "NIFTYBEES"}                 # the result (anything JSON can hold)

    job_id = start_job("etf_sweep", sweep, {"symbols": ["NIFTYBEES", "GOLDBEES"]})

    GET  /api/jobs/{job_id}          -> {"status", "progress", "message", "result", "error", ...}
    POST /api/jobs/{job_id}/cancel   -> asks the work to stop at its next progress() call

Everything is stored in the background_jobs table, so any page (or a second browser tab) can follow a job.
Cancelling is polite: the work stops the next time it reports progress (it is never killed half-way
through a database write). A job still "running" when the API restarts is marked failed.
"""
import datetime as dt
import json
import threading
import time
import uuid

from marketlens.broker.candles import IST
from marketlens.logger import get_logger, record_error

log = get_logger(__name__)
FINISHED = ("done", "failed", "cancelled")
SAVE_EVERY_SECONDS = 0.5          # progress is written at most twice a second (the page asks once a second)


class JobCancelled(Exception):
    """Raised inside the work when you pressed Cancel."""


class JobContext:
    """Handed to the work: report progress, and find out whether you pressed Cancel."""

    def __init__(self, job_id: str, url: str | None = None, clock=time.monotonic):
        self.job_id, self.url, self.clock = job_id, url, clock
        self._last_save = None

    # Report progress (0-100 %) and a short message; stops the work when Cancel was pressed.
    def progress(self, percent: float, message: str | None = None):
        now = self.clock()
        if self._last_save is None or now - self._last_save >= SAVE_EVERY_SECONDS or percent >= 100:
            self._last_save = now
            cancel = _update(self.job_id, self.url, stop_if_cancelled=True, progress=_clamp(percent), message=message)
            if cancel:
                raise JobCancelled()

    # True when Cancel was pressed (for work that wants to check without reporting progress).
    @property
    def cancelled(self) -> bool:
        job = get_job(self.job_id, self.url)
        return bool(job and job["cancel_requested"])

    # Stop now when Cancel was pressed.
    def check(self):
        if self.cancelled:
            raise JobCancelled()


# A percentage kept between 0 and 100.
def _clamp(percent) -> float:
    return round(min(max(float(percent), 0.0), 100.0), 1)


# Change a job row; returns True when Cancel was requested (stop_if_cancelled: then change nothing).
def _update(job_id: str, url: str | None, stop_if_cancelled: bool = False, **values) -> bool:
    from marketlens.db import get_session
    from marketlens.models import BackgroundJob
    with get_session(url) as session:
        row = session.get(BackgroundJob, job_id)
        if row is None:
            return False
        if stop_if_cancelled and row.cancel_requested:
            return True
        for key, value in values.items():
            if value is not None or key in ("result", "error"):
                setattr(row, key, value)
        session.commit()
        return bool(row.cancel_requested)


# Anything the work returned, as something JSON can hold (dates as text, NaN as null, tables as rows).
def _jsonable(result):
    import pandas as pd

    from marketlens.api.schemas import df_to_records
    if result is None:
        return None
    if isinstance(result, pd.DataFrame):
        return {"rows": df_to_records(result)}
    if isinstance(result, dict):
        return {str(k): _jsonable_value(v) for k, v in result.items()}
    return {"value": _jsonable_value(result)}


# One value inside a result.
def _jsonable_value(value):
    import pandas as pd

    from marketlens.api.schemas import df_to_records, to_json_value
    if isinstance(value, pd.DataFrame):
        return df_to_records(value)
    if isinstance(value, dict):
        return {str(k): _jsonable_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable_value(v) for v in value]
    value = to_json_value(value)
    json.dumps(value)                                      # raises TypeError for anything JSON cannot hold
    return value


# Run the work and record how it ended (in the background thread).
def _run(job_id: str, work, params: dict, url: str | None):
    _update(job_id, url, status="running", started_at=dt.datetime.now(IST), message="Started")
    try:
        result = work(JobContext(job_id, url), **params)
        _update(job_id, url, status="done", progress=100.0, result=_jsonable(result), message="Done",
                finished_at=dt.datetime.now(IST))
        log.info("Job %s finished", job_id)
    except JobCancelled:
        _update(job_id, url, status="cancelled", message="Cancelled by you", finished_at=dt.datetime.now(IST))
        log.info("Job %s cancelled", job_id)
    except Exception as err:
        friendly = str(err) if isinstance(err, ValueError) else \
            "The work stopped because of an unexpected error. The details are in logs/marketlens.log."
        record_error(__name__, f"Job {job_id} failed: {type(err).__name__}: {err}", {"job_id": job_id}, url=url)
        log.exception("Job %s failed", job_id)
        _update(job_id, url, status="failed", error=friendly, message="Failed", finished_at=dt.datetime.now(IST))


# Save a new job and start its work in a background thread; returns the job id at once.
def start_job(kind: str, work, params: dict | None = None, url: str | None = None, wait: bool = False) -> str:
    """wait=True runs it in this thread instead (tests, scripts)."""
    from marketlens.db import get_session
    from marketlens.models import BackgroundJob
    params = dict(params or {})
    job_id = uuid.uuid4().hex
    with get_session(url) as session:
        session.add(BackgroundJob(id=job_id, kind=kind, status="queued", params=_jsonable(params) or {},
                                  message="Waiting to start"))
        session.commit()
    log.info("Job %s (%s) queued", job_id, kind)
    if wait:
        _run(job_id, work, params, url)
    else:
        threading.Thread(target=_run, args=(job_id, work, params, url), name=f"job-{kind}-{job_id[:8]}",
                         daemon=True).start()
    return job_id


# A job as a dict for GET /api/jobs/{job_id}, or None when there is no such job.
def get_job(job_id: str, url: str | None = None) -> dict | None:
    from marketlens.db import get_session
    from marketlens.models import BackgroundJob
    with get_session(url) as session:
        row = session.get(BackgroundJob, job_id)
        if row is None:
            return None
        return {"job_id": row.id, "kind": row.kind, "status": row.status, "progress": float(row.progress),
                "message": row.message, "result": row.result if row.status == "done" else None,
                "error": row.error, "cancel_requested": row.cancel_requested, "finished": row.status in FINISHED,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None}


# Ask a job to stop; returns the job (unchanged when it had already finished), or None when unknown.
def cancel_job(job_id: str, url: str | None = None) -> dict | None:
    from marketlens.db import get_session
    from marketlens.models import BackgroundJob
    with get_session(url) as session:
        row = session.get(BackgroundJob, job_id)
        if row is None:
            return None
        if row.status == "queued":
            row.status, row.message, row.finished_at = "cancelled", "Cancelled by you", dt.datetime.now(IST)
        elif row.status == "running":
            row.cancel_requested, row.message = True, "Stopping..."
        session.commit()
    log.info("Job %s: cancel requested", job_id)
    return get_job(job_id, url)


# Jobs left queued / running by an API that stopped: mark them failed (called when the API starts).
def fail_orphans(url: str | None = None) -> int:
    from sqlalchemy import update

    from marketlens.db import get_session
    from marketlens.models import BackgroundJob
    with get_session(url) as session:
        count = session.execute(update(BackgroundJob).where(BackgroundJob.status.in_(("queued", "running")))
                                .values(status="failed", message="Failed",
                                        error="The API restarted while this job was running. Start it again.",
                                        finished_at=dt.datetime.now(IST))).rowcount
        session.commit()
    if count:
        log.warning("%s unfinished job(s) from before the restart marked failed", count)
    return count
