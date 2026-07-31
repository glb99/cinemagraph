"""FastAPI routes.

Routes only: parse the request, delegate to service.py, shape the response.
Every multi-step workflow lives in service.py instead -- the router/service
split that's standard practice for FastAPI projects, and which additionally
makes those workflows unit-testable without an HTTP round-trip. See
docs/DESIGN.md's decision log.

Renders run as FastAPI BackgroundTasks (no Celery/Redis -- this is a
single-process, local, no-auth personal tool) and write into a job-scoped
directory under CINEMAGRAPH_DATA_DIR. Config (that var, plus the three
optional-service URLs below) is a config.Settings instance, resolved once
per process via Depends(get_settings) rather than read from os.environ at
each call site -- see config.py's module docstring for why, and for the
one thing it deliberately does NOT cover (CINEMAGRAPH_LIBRARY_DIR, which
asset_library reads itself).

/render/video and /render/photo have full parity with the CLI's `make` and
`from-photo` commands (mask upload, per-effect overrides via
cinemagraph.validation, loop_duration, etc.) -- both entry points validate
through the same functions and reject the same bad combinations, just
translated into a click.UsageError on one side and an HTTP 422 on the other.
/mask-preview is the API equivalent of `cinemagraph mask-preview`.

Routes that talk to optional external services do so via
_external_service.py's shared call_optional_service()/service_available()
helpers, both of which degrade to a clean 503/false rather than erroring
whenever the service isn't configured or isn't reachable, checked fresh on
every request:

- /mask/semantic proxies to the machine-learning/ CLIPSeg service
  (ML_SERVICE_URL). POST /render/photo's mask_prompt field chains that same
  segmentation straight into a render.
- /generate/music proxies to an ACE-Step API server (ACESTEP_URL) -- that
  one's a real job-queue API (release_task -> poll query_result -> download
  via /v1/audio), which is why it reuses this app's own job system
  (server/jobs.py, GET /jobs/{id}, GET /jobs/{id}/file) rather than needing new
  status-tracking infrastructure: "poll a remote job queue and download the
  result" turned out to fit the same Job abstraction already built for
  local renders.
- /generate/sound-effect proxies to the sound-effects/ service
  (SOUND_EFFECTS_URL) -- unlike ACE-Step, that service's own /generate call
  is synchronous (one request, one response with the finished audio), but
  it still runs as a background job since generation genuinely takes tens of
  seconds to minutes and the caller shouldn't hold the connection open.
- /generate/image proxies to an ImageGenerator adapter (generation_ports.py/
  generation_adapters.py), defaulting to SDXLAdapter (local SDXL,
  IMAGE_GENERATION_URL) -- same synchronous-call shape as
  /generate/sound-effect. A hosted API (Gemini's native image models) was
  tried first and reverted: new Google AI Studio accounts require a
  non-refundable minimum prepay to use it at all, found only by actually
  trying to generate an image. See docs/experiments/ for the full account,
  and docs/DESIGN.md sec 3.6 for why that history justifies the port/
  registry indirection here rather than the previous inline HTTP call.
  Accepts an optional `reference_image` upload (img2img: generate
  conditioned on a reference photo instead of pure text) plus `strength`
  (how far the result may deviate from it) -- see run_image_job's own
  docstring.
"""
import shutil
import tempfile
from pathlib import Path
from typing import Annotated

import asset_library as library
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from cinemagraph import effects as effects_pkg, pipeline, validation

from . import jobs, service, ui
from ._external_service import call_optional_service, service_available
from .config import Settings, get_settings
from .generation_adapters import GeminiAdapter, SDXLAdapter
from .generation_registry import available_image_generators, register_image_generator
from .schemas import AssetResponse, CapabilitiesResponse, JobResponse, JobStatusResponse

SettingsDep = Annotated[Settings, Depends(get_settings)]

app = FastAPI(title="cinemagraph-tool API")

# Populates generation_registry for real (sec 3.6) -- "sdxl" always
# registers (image-generation/'s own reachability is still checked fresh
# per request via service_available, same as before); "gemini" only
# registers when GEMINI_API_KEY is actually configured, the same
# "absent -> just don't offer it" degrade every other optional capability
# already follows. run_image_job's own default (server/service.py) resolves
# a model name through this same registry -- see its docstring for why that
# doesn't shadow a test's own Settings(...) for real request traffic.
_settings = get_settings()
register_image_generator("sdxl", SDXLAdapter(_settings))
if _settings.gemini_api_key:
    register_image_generator("gemini", GeminiAdapter(_settings))


def _job_dir(settings: Settings, job_id: str) -> Path:
    job_dir = settings.data_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


async def _save_upload(upload: UploadFile, dest: Path) -> None:
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)


@app.get("/", response_class=HTMLResponse)
async def index():
    """The thin web UI (photo rendering only for now -- see ui.py). Not a
    packaging concern like static files would be: this is plain Python
    source, included in every build the same as any other module.
    """
    return ui.INDEX_HTML


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(settings: SettingsDep):
    """image_generation is true if *either* SDXL health-checks (a real,
    fresh-per-request call, same as every other satellite) or Gemini is
    registered (no equivalent health check exists for a hosted API -- its
    registration, gated on GEMINI_API_KEY at app startup in this module, is
    the only signal available). image_generation_models lists every
    registered adapter regardless of live reachability -- same "checked
    fresh at actual request time" philosophy as the rest of this file: a
    registered-but-currently-unreachable SDXL still shows up here, and a
    real generate request against it still degrades to a clear per-job
    error, exactly as before this adapter existed.
    """
    return CapabilitiesResponse(
        semantic_mask=await service_available(settings.semantic_mask_service),
        music_generation=await service_available(settings.music_service),
        sound_effect_generation=await service_available(settings.sound_effect_service),
        image_generation=(
            await service_available(settings.image_generation_service)
            or bool(settings.gemini_api_key)
        ),
        image_generation_models=list(available_image_generators()),
    )


@app.get("/effects")
async def list_effects():
    return {"effects": list(effects_pkg.EFFECTS)}


@app.post("/render/video", response_model=JobResponse)
async def render_video(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    input_file: UploadFile = File(...),
    mask: UploadFile | None = File(None),
    still_frame_index: int = Form(0),
    blend_frames: int = Form(10),
    auto_trim: bool = Form(True),
    mask_threshold: int = Form(25),
    feather: int = Form(21),
    apply_grade: bool = Form(True),
    grade_strength: float = Form(1.0),
    grain: float = Form(0.03),
    also_gif: bool = Form(False),
    loop_duration: float | None = Form(None),
):
    if loop_duration and also_gif:
        raise HTTPException(422, pipeline._LOOP_DURATION_GIF_ERROR)

    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    input_path = job_dir / (input_file.filename or "input")
    await _save_upload(input_file, input_path)
    output_path = job_dir / "output.mp4"

    mask_path = None
    if mask is not None:
        mask_path = job_dir / (mask.filename or "mask.png")
        await _save_upload(mask, mask_path)

    background_tasks.add_task(
        service.run_render_job, job.id, pipeline.save_cinemagraph_video, output_path,
        library_kind="generated", library_tags=["video"],
        input_path=str(input_path), mask_path=str(mask_path) if mask_path else None,
        still_frame_index=still_frame_index, blend_frames=blend_frames, auto_trim_loop=auto_trim,
        mask_threshold=mask_threshold, feather=feather,
        apply_grade=apply_grade, grade_strength=grade_strength, grain=grain, also_gif=also_gif,
        loop_duration=loop_duration,
    )
    return JobResponse(job_id=job.id)


@app.post("/render/photo", response_model=JobResponse)
async def render_photo(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    input_file: UploadFile = File(...),
    effect: list[str] = Form(...),
    mask: UploadFile | None = File(None),
    mask_prompt: str | None = Form(None),
    duration: float = Form(4.0),
    fps: int = Form(30),
    speed: float = Form(1.0),
    rain_count: int | None = Form(None),
    rain_opacity: float | None = Form(None),
    snow_count: int | None = Form(None),
    snow_opacity: float | None = Form(None),
    dust_count: int | None = Form(None),
    dust_opacity: float | None = Form(None),
    ripple_amplitude: float | None = Form(None),
    ripple_wavelength: float | None = Form(None),
    sway_amplitude: float | None = Form(None),
    sway_freq: float | None = Form(None),
    wind_amplitude: float | None = Form(None),
    wind_gustiness: float | None = Form(None),
    flicker_strength: float | None = Form(None),
    smoke_opacity: float | None = Form(None),
    vapor_opacity: float | None = Form(None),
    feather: int = Form(21),
    apply_grade: bool = Form(True),
    grade_strength: float = Form(1.0),
    grain: float = Form(0.03),
    also_gif: bool = Form(False),
    loop_duration: float | None = Form(None),
):
    """Render a photo cinemagraph.

    Supply at most one of `mask` (a hand-painted mask file, same convention
    as the CLI's --mask) or `mask_prompt` (e.g. "clouds", "sun" -- segments
    the photo via the machine-learning service first and renders with that
    mask; the job errors with a 503-style message if that service isn't
    configured/reachable, rather than silently rendering unmasked). Per-effect
    overrides (rain_count, ripple_amplitude, etc.) mirror the CLI's per-effect
    flags one-for-one and go through the same validation.resolve_effect_kwargs
    check, so an override for an effect that wasn't requested is a 422 here
    exactly as it's a click.UsageError on the CLI.
    """
    unknown = [e for e in effect if e not in effects_pkg.EFFECTS]
    if unknown:
        raise HTTPException(422, f"Unknown effect(s) {unknown}. Choose from: {', '.join(effects_pkg.EFFECTS)}")
    if mask is not None and mask_prompt:
        raise HTTPException(422, "Supply either `mask` or `mask_prompt`, not both.")
    if loop_duration and also_gif:
        raise HTTPException(422, pipeline._LOOP_DURATION_GIF_ERROR)

    per_effect_options = {
        "rain": {"count": rain_count, "opacity": rain_opacity},
        "snow": {"count": snow_count, "opacity": snow_opacity},
        "dust": {"count": dust_count, "opacity": dust_opacity},
        "ripple": {"amplitude": ripple_amplitude, "wavelength": ripple_wavelength},
        "sway": {"amplitude": sway_amplitude, "freq": sway_freq},
        "wind": {"amplitude": wind_amplitude, "gustiness": wind_gustiness},
        "flicker": {"strength": flicker_strength},
        "smoke": {"opacity": smoke_opacity},
        "vapor": {"opacity": vapor_opacity},
    }
    try:
        effect_kwargs = validation.resolve_effect_kwargs(effect, per_effect_options)
    except ValueError as e:
        raise HTTPException(422, str(e))

    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    input_path = job_dir / (input_file.filename or "input")
    await _save_upload(input_file, input_path)
    output_path = job_dir / "output.mp4"

    render_kwargs = dict(
        effect=effect, duration=duration, fps=fps, speed=speed, feather=feather,
        apply_grade=apply_grade, grade_strength=grade_strength, grain=grain, also_gif=also_gif,
        effect_kwargs=effect_kwargs, loop_duration=loop_duration,
    )

    if mask_prompt:
        background_tasks.add_task(
            service.run_photo_semantic_mask_job, job.id, output_path,
            settings=settings, photo_path=input_path, mask_path=job_dir / "mask.png",
            mask_prompt=mask_prompt, library_kind="generated", library_tags=["photo", *effect],
            **render_kwargs,
        )
    else:
        mask_path = None
        if mask is not None:
            mask_path = job_dir / (mask.filename or "mask.png")
            await _save_upload(mask, mask_path)
        background_tasks.add_task(
            service.run_render_job, job.id, pipeline.save_cinemagraph_from_photo, output_path,
            library_kind="generated", library_tags=["photo", *effect],
            photo_path=str(input_path), mask_path=str(mask_path) if mask_path else None,
            **render_kwargs,
        )
    return JobResponse(job_id=job.id)


@app.post("/mask-preview", response_model=JobResponse)
async def mask_preview(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    input_file: UploadFile = File(...),
    mask_threshold: int = Form(25),
    feather: int = Form(21),
):
    """Preview the auto-detected motion mask for a video clip as a PNG,
    without rendering the full cinemagraph -- the API equivalent of
    `cinemagraph mask-preview`. Useful for checking/tuning mask_threshold
    before committing to a full /render/video call.
    """
    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    input_path = job_dir / (input_file.filename or "input")
    await _save_upload(input_file, input_path)
    output_path = job_dir / "mask.png"

    background_tasks.add_task(
        service.run_render_job, job.id, pipeline.save_mask_preview, output_path,
        input_path=str(input_path), mask_threshold=mask_threshold, feather=feather,
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
async def semantic_mask(settings: SettingsDep, image: UploadFile = File(...), prompt: str = Form(...)):
    """Proxies to the machine-learning (CLIPSeg) service. Returns a clean 503,
    not a 500, whenever that service isn't configured or isn't reachable --
    see machine-learning/README.md for the /segment contract this proxies.
    """
    files = {"image": (image.filename, await image.read(), image.content_type)}
    resp = await call_optional_service(
        settings.semantic_mask_service, "POST", "/segment",
        data={"prompt": prompt}, files=files, timeout=60.0,
    )
    return Response(content=resp.content, media_type="image/png")


@app.post("/generate/music", response_model=JobResponse)
async def generate_music(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    prompt: str = Form(""),
    lyrics: str = Form(""),
    duration: float = Form(30.0),
    thinking: bool = Form(True),
    instrumental: bool = Form(False),
):
    """Proxies to an ACE-Step API server. Same degrade-cleanly contract as
    /mask/semantic: reuses this file's own job system rather than adding new
    status-tracking routes -- GET /jobs/{job_id} and /jobs/{job_id}/file
    already work for this without any changes.

    `instrumental` overrides whatever `lyrics` was submitted -- see
    run_music_job's docstring for why that's the only reliable way to get
    ACE-Step to actually omit vocals (an empty lyrics field doesn't).
    """
    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.mp3"

    background_tasks.add_task(
        service.run_music_job, job.id, output_path,
        settings=settings, prompt=prompt, lyrics=lyrics, duration=duration, thinking=thinking,
        instrumental=instrumental, library_kind="generated",
    )
    return JobResponse(job_id=job.id)


@app.post("/generate/sound-effect", response_model=JobResponse)
async def generate_sound_effect(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    prompt: str = Form(...),
    duration: float = Form(10.0),
):
    """Proxies to the sound-effects/ service (Stable Audio Open). Unlike
    ACE-Step, that service answers in one synchronous call -- still run as
    a background job here since generation takes real time and shouldn't
    block the HTTP connection.
    """
    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.wav"

    background_tasks.add_task(
        service.run_sound_effect_job, job.id, output_path,
        settings=settings, prompt=prompt, duration=duration, library_kind="generated",
    )
    return JobResponse(job_id=job.id)


@app.post("/generate/image", response_model=JobResponse)
async def generate_image(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    prompt: str = Form(...),
    reference_image: UploadFile | None = File(None),
    strength: float = Form(0.6),
    model: str = Form("sdxl"),
):
    """Proxies to a registered ImageGenerator adapter (generation_ports.py) --
    `model` picks which one ("sdxl" = local SDXL/image-generation/, "gemini"
    = Google's hosted API/generation/, when GEMINI_API_KEY is configured).
    Same background-job shape as /generate/sound-effect either way --
    generation takes real time and shouldn't hold the HTTP connection open.

    `reference_image` is optional (img2img mode -- see run_image_job's own
    docstring); `strength` only matters for SDXL. Saved to the job directory
    before scheduling the background task, same as /render/photo's mask
    upload -- the UploadFile itself doesn't survive past this request.
    """
    if model not in available_image_generators():
        raise HTTPException(
            422, f"Unknown model '{model}'. Choose from: {', '.join(available_image_generators())}"
        )

    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.png"

    reference_image_path = None
    if reference_image is not None:
        reference_image_path = job_dir / (reference_image.filename or "reference.png")
        await _save_upload(reference_image, reference_image_path)

    background_tasks.add_task(
        service.run_image_job, job.id, output_path,
        settings=settings, prompt=prompt,
        reference_image_path=reference_image_path, strength=strength, model=model,
        library_kind="generated",
    )
    return JobResponse(job_id=job.id)
