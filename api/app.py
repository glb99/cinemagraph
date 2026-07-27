"""FastAPI entry point.

Kept deliberately thin: routes only parse the request, delegate to
cinemagraph.pipeline / api.jobs / api._external_service, and shape the
response. No business logic lives here -- see CLAUDE.md's note on
entry-point-module discipline (the same principle cli.py already follows).

Renders run as FastAPI BackgroundTasks (no Celery/Redis -- this is a
single-process, local, no-auth personal tool) and write into a job-scoped
directory under CINEMAGRAPH_DATA_DIR.

Three routes talk to optional external services via _external_service.py's
shared call_optional_service()/service_available() helpers, both of which
degrade to a clean 503/false rather than erroring whenever the service
isn't configured or isn't reachable, checked fresh on every request:

- /mask/semantic proxies to a not-yet-built CLIPSeg service (ML_SERVICE_URL;
  see machine-learning/README.md).
- /generate/music proxies to an ACE-Step API server (ACESTEP_URL) -- that
  one's a real job-queue API (release_task -> poll query_result -> download
  via /v1/audio), which is why it reuses this file's own job system
  (api/jobs.py, GET /jobs/{id}, GET /jobs/{id}/file) rather than needing new
  status-tracking infrastructure: "poll a remote job queue and download the
  result" turned out to fit the same Job abstraction already built for
  local renders.
- /generate/sound-effect proxies to the sound-effects/ service
  (SOUND_EFFECTS_URL) -- unlike ACE-Step, that service's own /generate call
  is synchronous (one request, one response with the finished audio), but
  it still runs as a background job here since generation genuinely takes
  tens of seconds to minutes and the caller shouldn't have to hold the
  connection open for that.
"""
import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from cinemagraph import effects as effects_pkg, library, pipeline, validation

from . import jobs
from ._external_service import call_optional_service, service_available
from .schemas import AssetResponse, CapabilitiesResponse, JobResponse, JobStatusResponse

DATA_DIR = Path(os.environ.get("CINEMAGRAPH_DATA_DIR", "./data")).resolve()

MUSIC_POLL_INTERVAL_SECONDS = 2.0
MUSIC_POLL_MAX_ATTEMPTS = 150  # ~5 minutes total

app = FastAPI(title="cinemagraph-tool API")


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
    return CapabilitiesResponse(
        semantic_mask=await service_available("ML_SERVICE_URL"),
        music_generation=await service_available("ACESTEP_URL"),
        sound_effect_generation=await service_available("SOUND_EFFECTS_URL"),
    )


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
    """Proxies to the machine-learning (CLIPSeg) service. Returns a clean 503,
    not a 500, whenever that service isn't configured or isn't reachable --
    see machine-learning/README.md for the /segment contract this proxies.
    """
    files = {"image": (image.filename, await image.read(), image.content_type)}
    resp = await call_optional_service(
        "ML_SERVICE_URL", "POST", "/segment", service_name="Semantic masking",
        data={"prompt": prompt}, files=files, timeout=60.0,
    )
    return Response(content=resp.content, media_type="image/png")


async def _run_music_job(job_id: str, output_path: Path, *, prompt: str, lyrics: str, duration: float, thinking: bool) -> None:
    """Drives ACE-Step's own job-queue API to completion: create a task,
    poll until it reports success/failure, download the resulting audio.
    ACE-Step's response envelope wraps everything in {"data": ..., "code": ...};
    see docs/DESIGN.md / the acestep-docs skill's API.md for the full contract.
    """
    jobs.mark_running(job_id)
    try:
        create_resp = await call_optional_service(
            "ACESTEP_URL", "POST", "/release_task", service_name="Music generation",
            json={"prompt": prompt, "lyrics": lyrics, "audio_duration": duration, "thinking": thinking},
        )
        task_id = create_resp.json()["data"]["task_id"]

        result = None
        for _ in range(MUSIC_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(MUSIC_POLL_INTERVAL_SECONDS)
            query_resp = await call_optional_service(
                "ACESTEP_URL", "POST", "/query_result", service_name="Music generation",
                json={"task_id_list": [task_id]},
            )
            entry = query_resp.json()["data"][0]
            if entry["status"] == 1:
                result = json.loads(entry["result"])[0]
                break
            if entry["status"] == 2:
                raise RuntimeError("ACE-Step reported generation failure.")
        if result is None:
            raise RuntimeError(f"ACE-Step generation timed out after {MUSIC_POLL_MAX_ATTEMPTS} polls.")

        audio_resp = await call_optional_service(
            "ACESTEP_URL", "GET", result["file"], service_name="Music generation", timeout=60.0,
        )
        output_path.write_bytes(audio_resp.content)
        jobs.mark_done(job_id, output_path)
    except HTTPException as e:
        jobs.mark_error(job_id, e.detail)
    except Exception as e:
        jobs.mark_error(job_id, str(e))


@app.post("/generate/music", response_model=JobResponse)
async def generate_music(
    background_tasks: BackgroundTasks,
    prompt: str = Form(""),
    lyrics: str = Form(""),
    duration: float = Form(30.0),
    thinking: bool = Form(True),
):
    """Proxies to an ACE-Step API server. Same degrade-cleanly contract as
    /mask/semantic: reuses this file's own job system rather than adding new
    status-tracking routes -- GET /jobs/{job_id} and /jobs/{job_id}/file
    already work for this without any changes.
    """
    job = jobs.create_job()
    job_dir = _job_dir(job.id)
    output_path = job_dir / "output.mp3"

    background_tasks.add_task(
        _run_music_job, job.id, output_path,
        prompt=prompt, lyrics=lyrics, duration=duration, thinking=thinking,
    )
    return JobResponse(job_id=job.id)


async def _run_sound_effect_job(job_id: str, output_path: Path, *, prompt: str, duration: float) -> None:
    jobs.mark_running(job_id)
    try:
        resp = await call_optional_service(
            "SOUND_EFFECTS_URL", "POST", "/generate", service_name="Sound effect generation",
            json={"prompt": prompt, "duration": duration}, timeout=300.0,
        )
        output_path.write_bytes(resp.content)
        jobs.mark_done(job_id, output_path)
    except HTTPException as e:
        jobs.mark_error(job_id, e.detail)
    except Exception as e:
        jobs.mark_error(job_id, str(e))


@app.post("/generate/sound-effect", response_model=JobResponse)
async def generate_sound_effect(
    background_tasks: BackgroundTasks,
    prompt: str = Form(...),
    duration: float = Form(10.0),
):
    """Proxies to the sound-effects/ service (Stable Audio Open). Unlike
    ACE-Step, that service answers in one synchronous call -- still run as
    a background job here since generation takes real time and shouldn't
    block the HTTP connection.
    """
    job = jobs.create_job()
    job_dir = _job_dir(job.id)
    output_path = job_dir / "output.wav"

    background_tasks.add_task(
        _run_sound_effect_job, job.id, output_path, prompt=prompt, duration=duration,
    )
    return JobResponse(job_id=job.id)
