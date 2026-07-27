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
