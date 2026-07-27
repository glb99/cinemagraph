"""FastAPI entry point.

Kept deliberately thin: routes only parse the request, delegate to
cinemagraph.pipeline / api.jobs, and shape the response. No business logic
lives here -- see CLAUDE.md's note on entry-point-module discipline (the
same principle cli.py already follows).

Renders run as FastAPI BackgroundTasks (no Celery/Redis -- this is a
single-process, local, no-auth personal tool) and write into a job-scoped
directory under CINEMAGRAPH_DATA_DIR. The semantic-mask route is a stub for
a not-yet-built CLIPSeg service (see machine-learning/README.md): it
degrades to a clean 503 rather than an error whenever ML_SERVICE_URL isn't
set or isn't reachable, checked fresh on every request rather than once at
startup, so the API never fails to start just because that (optional)
service isn't running.
"""
import os
import shutil
import tempfile
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from cinemagraph import effects as effects_pkg, library, pipeline, validation

from . import jobs
from .schemas import AssetResponse, CapabilitiesResponse, JobResponse, JobStatusResponse

DATA_DIR = Path(os.environ.get("CINEMAGRAPH_DATA_DIR", "./data")).resolve()

app = FastAPI(title="cinemagraph-tool API")


def _ml_service_url() -> str | None:
    return os.environ.get("ML_SERVICE_URL")


def _job_dir(job_id: str) -> Path:
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


async def _save_upload(upload: UploadFile, dest: Path) -> None:
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)


def _run_job(job_id: str, render_fn, output_path: Path, **kwargs) -> None:
    jobs.mark_running(job_id)
    try:
        render_fn(output_path=str(output_path), **kwargs)
        jobs.mark_done(job_id, output_path)
    except Exception as e:
        jobs.mark_error(job_id, str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities():
    service_url = _ml_service_url()
    semantic_mask = False
    if service_url:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{service_url}/health")
                semantic_mask = resp.status_code == 200
        except httpx.HTTPError:
            semantic_mask = False
    return CapabilitiesResponse(semantic_mask=semantic_mask)


@app.get("/effects")
async def list_effects():
    return {"effects": list(effects_pkg.EFFECTS)}


@app.post("/render/video", response_model=JobResponse)
async def render_video(
    background_tasks: BackgroundTasks,
    input_file: UploadFile = File(...),
    mask_threshold: int = Form(25),
    feather: int = Form(21),
    apply_grade: bool = Form(True),
    grade_strength: float = Form(1.0),
    grain: float = Form(0.03),
    also_gif: bool = Form(False),
):
    job = jobs.create_job()
    job_dir = _job_dir(job.id)
    input_path = job_dir / (input_file.filename or "input")
    await _save_upload(input_file, input_path)
    output_path = job_dir / "output.mp4"

    background_tasks.add_task(
        _run_job, job.id, pipeline.save_cinemagraph_video, output_path,
        input_path=str(input_path), mask_threshold=mask_threshold, feather=feather,
        apply_grade=apply_grade, grade_strength=grade_strength, grain=grain, also_gif=also_gif,
    )
    return JobResponse(job_id=job.id)


@app.post("/render/photo", response_model=JobResponse)
async def render_photo(
    background_tasks: BackgroundTasks,
    input_file: UploadFile = File(...),
    effect: list[str] = Form(...),
    duration: float = Form(4.0),
    fps: int = Form(30),
    speed: float = Form(1.0),
    feather: int = Form(21),
    apply_grade: bool = Form(True),
    grade_strength: float = Form(1.0),
    grain: float = Form(0.03),
    also_gif: bool = Form(False),
):
    unknown = [e for e in effect if e not in effects_pkg.EFFECTS]
    if unknown:
        raise HTTPException(422, f"Unknown effect(s) {unknown}. Choose from: {', '.join(effects_pkg.EFFECTS)}")

    job = jobs.create_job()
    job_dir = _job_dir(job.id)
    input_path = job_dir / (input_file.filename or "input")
    await _save_upload(input_file, input_path)
    output_path = job_dir / "output.mp4"

    background_tasks.add_task(
        _run_job, job.id, pipeline.save_cinemagraph_from_photo, output_path,
        photo_path=str(input_path), effect=effect, duration=duration, fps=fps, speed=speed,
        feather=feather, apply_grade=apply_grade, grade_strength=grade_strength, grain=grain,
        also_gif=also_gif,
    )
    return JobResponse(job_id=job.id)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def job_status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value,
        output_path=str(job.output_path) if job.output_path else None,
        error=job.error,
    )


@app.get("/jobs/{job_id}/file")
async def job_file(job_id: str):
    job = jobs.get_job(job_id)
    if job is None or job.status != jobs.JobStatus.DONE or job.output_path is None:
        raise HTTPException(404, "Output not available")
    return FileResponse(str(job.output_path))


def _asset_to_response(asset) -> AssetResponse:
    return AssetResponse(
        id=asset.id, kind=asset.kind, original_filename=asset.original_filename,
        added_at=asset.added_at, tags=asset.tags, provenance=asset.provenance,
    )


@app.post("/library", response_model=AssetResponse)
async def library_add(
    upload: UploadFile = File(...),
    kind: str = Form("reference"),
    tags: str = Form(""),
):
    if kind not in library.KINDS:
        raise HTTPException(422, f"Unknown kind '{kind}'. Choose from: {', '.join(library.KINDS)}")

    with tempfile.NamedTemporaryFile(suffix=Path(upload.filename or "").suffix, delete=False) as tmp:
        tmp.write(await upload.read())
        tmp_path = Path(tmp.name)
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        asset = library.add(str(tmp_path), kind=kind, tags=tag_list, original_filename=upload.filename)
    finally:
        tmp_path.unlink(missing_ok=True)
    return _asset_to_response(asset)


@app.get("/library", response_model=list[AssetResponse])
async def library_list(kind: str | None = None, tag: str | None = None):
    return [_asset_to_response(a) for a in library.list_assets(kind=kind, tag=tag)]


@app.get("/library/{asset_id}", response_model=AssetResponse)
async def library_get(asset_id: str):
    asset = library.get(asset_id)
    if asset is None:
        raise HTTPException(404, "Asset not found")
    return _asset_to_response(asset)


@app.get("/library/{asset_id}/file")
async def library_file(asset_id: str):
    asset = library.get(asset_id)
    if asset is None:
        raise HTTPException(404, "Asset not found")
    return FileResponse(str(asset.path))


@app.delete("/library/{asset_id}")
async def library_remove(asset_id: str):
    removed = library.remove(asset_id)
    if not removed:
        raise HTTPException(404, "Asset not found")
    return {"removed": asset_id}


@app.post("/mask/semantic")
async def semantic_mask(image: UploadFile = File(...), prompt: str = Form(...)):
    """Proxies to the (not-yet-built) CLIPSeg service. Returns a clean 503,
    not a 500, whenever that service isn't configured or isn't reachable --
    this is the seam that feature will plug into; see machine-learning/README.md.
    """
    service_url = _ml_service_url()
    if not service_url:
        raise HTTPException(503, "Semantic masking is unavailable: no ML service configured.")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"image": (image.filename, await image.read(), image.content_type)}
            resp = await client.post(f"{service_url}/segment", data={"prompt": prompt}, files=files)
            resp.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(503, "Semantic masking is unavailable: ML service unreachable.")
    return resp.json()
