"""GET /jobs/{job_id}, GET /jobs/{job_id}/file, POST /jobs/{job_id}/save."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse

from .. import jobs as jobs_module
from .. import service
from .._route_helpers import _asset_to_response
from ..schemas import AssetResponse, JobStatusResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobStatusResponse)
def job_status(job_id: str) -> JobStatusResponse:
    job = jobs_module.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value,
        output_path=str(job.output_path) if job.output_path else None,
        error=job.error,
        can_save=job.pending_library is not None,
        saved_asset_id=job.saved_asset_id,
    )


@router.get("/{job_id}/file")
def job_file(job_id: str) -> FileResponse:
    job = jobs_module.get_job(job_id)
    if (
        job is None
        or job.status != jobs_module.JobStatus.DONE
        or job.output_path is None
    ):
        raise HTTPException(404, "Output not available")
    return FileResponse(str(job.output_path))


@router.post("/{job_id}/save", response_model=AssetResponse)
def save_job(
    job_id: str, project: Annotated[str | None, Form()] = None
) -> AssetResponse:
    """Registers a completed job's output into the asset library on demand
    -- the explicit, post-generation "keep this" action that replaced an
    earlier pre-generation `save_to_library` toggle (deciding whether to
    keep a generation before seeing/hearing it was the wrong shape). See
    service.save_job_to_library's own docstring for the staging mechanism
    this consumes.

    `project` (optional) assigns the saved asset to a project right here --
    the save moment doubles as the project-assignment moment, per the same
    project owner decision that put saving itself after generation instead
    of before.
    """
    try:
        asset = service.save_job_to_library(job_id, project=project or None)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _asset_to_response(asset)
