"""In-process job tracking for background renders.

Deliberately just a dict in memory -- no Redis, no Celery. This is a
single-process, local, no-auth personal tool (see the architecture plan);
job state living only as long as the process does is an accepted tradeoff,
not an oversight. Revisit only if this ever needs to run with multiple
worker processes, which is explicitly out of scope for now.
"""
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.PENDING
    output_path: Path | None = None
    error: str | None = None
    # Set at completion (stage_for_library, below) to whatever this job's
    # output *would* be registered as, without actually registering it --
    # the save/discard decision moved to a later, explicit POST
    # /jobs/{id}/save, so nothing gets auto-added to the library anymore.
    # None means either "not staged yet" or "this job type never offers
    # saving" (e.g. /mask-preview) or "already saved" (cleared on save).
    pending_library: dict | None = None
    saved_asset_id: str | None = None


_JOBS: dict[str, Job] = {}


def create_job() -> Job:
    job = Job(id=str(uuid.uuid4()))
    _JOBS[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def mark_running(job_id: str) -> None:
    _JOBS[job_id].status = JobStatus.RUNNING


def mark_done(job_id: str, output_path: Path) -> None:
    job = _JOBS[job_id]
    job.status = JobStatus.DONE
    job.output_path = output_path


def mark_error(job_id: str, error: str) -> None:
    job = _JOBS[job_id]
    job.status = JobStatus.ERROR
    job.error = error


def stage_for_library(job_id: str, kind: str | None, tags: list[str] | None, provenance: dict | None) -> None:
    """Attaches candidate library metadata to a completed job -- does NOT
    register it in the asset library yet (see save_job_to_library in
    service.py for the actual write, triggered by POST /jobs/{id}/save).
    `kind=None` means this job type never offers saving at all -- the same
    meaning `library_kind=None` already had back when this call site
    registered immediately instead of staging."""
    if kind is None:
        return
    _JOBS[job_id].pending_library = {"kind": kind, "tags": tags or [], "provenance": provenance}
