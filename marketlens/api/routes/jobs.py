"""Background jobs: follow and cancel slow work started by other endpoints (see marketlens/api/jobs.py).

    GET  /api/jobs/{job_id}          status, progress %, message, and the result when done
    POST /api/jobs/{job_id}/cancel   ask the work to stop
"""
from fastapi import APIRouter, Request

from marketlens.api import jobs
from marketlens.api.errors import ApiError

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# Status, progress %, message and (when done) the result of one job.
@router.get("/{job_id}")
def job_status(job_id: str, request: Request) -> dict:
    """The page asks this once a second while it shows the progress bar."""
    job = jobs.get_job(job_id, getattr(request.app.state, "url", None))
    if job is None:
        raise ApiError(f"There is no job {job_id}. It may be from another database.", 404)
    return job


# Ask a job to stop (it stops at its next progress report).
@router.post("/{job_id}/cancel")
def job_cancel(job_id: str, request: Request) -> dict:
    """Cancel: a queued job is cancelled at once, a running one at its next progress report."""
    job = jobs.cancel_job(job_id, getattr(request.app.state, "url", None))
    if job is None:
        raise ApiError(f"There is no job {job_id}.", 404)
    return job
