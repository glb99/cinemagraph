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

/render/photo has one API-only capability beyond that parity: its input can
be an existing library asset (`input_asset_id`, resolved via
`asset_library.get()`) instead of a fresh upload (`input_file`) -- exactly
one of the two, same "pick one or the other" pattern already used for
`mask`/`mask_prompt` on this route. Web-UI-only by design (the CLI already
works with any file path, including a library asset's own stored path).

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
- /assemble combines already-generated library assets (clips, music, sound
  effects) into one finished video via assembly.pipeline.assemble --
  crossfades between clips, crossfades between songs plus edge fades, sound
  effects layered continuously under the music. Not one of the "optional
  external service" routes above (no satellite container involved, no
  service_available() check) -- purely local ffmpeg subprocess work. See
  docs/DESIGN.md sec 5.6.
- /projects (GET/rename/DELETE) and /library/{id}/project manage the
  reserved-prefix "project:*" tag asset_library uses to group content
  (see its own module docstring) -- no new table, just a friendlier surface
  over tag filtering that already existed. POST /jobs/{id}/save also takes
  an optional `project` field, the primary way an asset gets assigned to one
  in the first place (at the moment it's actually kept, not before).
"""

import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, Response

import asset_library as library
from cinemagraph import effects as effects_pkg
from cinemagraph import pipeline, validation

from . import jobs, service
from ._external_service import call_optional_service, service_available
from .config import Settings, get_settings
from .generation_adapters import (
    ACEStepAdapter,
    GeminiAdapter,
    Lyria3Adapter,
    SDXLAdapter,
    StableAudioAdapter,
)
from .generation_registry import (
    available_image_generators,
    available_music_generators,
    available_music_remix_generators,
    register_image_generator,
    register_music_generator,
    register_sound_effect_generator,
)
from .schemas import AssetResponse, CapabilitiesResponse, JobResponse, JobStatusResponse

SettingsDep = Annotated[Settings, Depends(get_settings)]

app = FastAPI(title="cinemagraph-tool API")

# Populates generation_registry for real (sec 3.6) -- "sdxl"/"acestep" always
# register (their own satellite reachability is still checked fresh per
# request via service_available, same as before); "gemini"/"lyria3" only
# register when GEMINI_API_KEY is actually configured, the same
# "absent -> just don't offer it" degrade every other optional capability
# already follows -- both are hosted APIs behind the same key, not a
# self-hosted GPU service. run_image_job's/run_music_job's own defaults
# (server/service.py) resolve a model name through this same registry -- see
# their docstrings for why that doesn't shadow a test's own Settings(...)
# for real request traffic.
#
# "stable-audio" registers unconditionally too (same as "sdxl"/"acestep") --
# reachability is still checked fresh per request either way; nothing in
# server/service.py looks it up by name yet (no second sound-effect backend
# exists), the same "mechanism present, unconsumed" phase image and music
# generation themselves started in. See generation_registry.py's own docstring.
_settings = get_settings()
register_image_generator("sdxl", SDXLAdapter(_settings))
register_music_generator("acestep", ACEStepAdapter(_settings))
if _settings.gemini_api_key:
    register_image_generator("gemini", GeminiAdapter(_settings))
    register_music_generator("lyria3", Lyria3Adapter(_settings))
register_sound_effect_generator("stable-audio", StableAudioAdapter(_settings))


def _job_dir(settings: Settings, job_id: str) -> Path:
    job_dir = settings.data_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


async def _save_upload(upload: UploadFile, dest: Path) -> None:
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)


async def _resolve_optional_audio_input(
    upload: UploadFile | None,
    asset_id: str | None,
    job_dir: Path,
    field_name: str,
) -> Path | None:
    """Generalizes /render/photo's input_file-xor-input_asset_id pattern to
    an *optional* pair (neither given is valid here, unlike /render/photo's
    input which requires exactly one) -- /generate/music needs this twice
    (source audio, reference audio), both genuinely optional on their own.
    """
    if upload is not None and asset_id is not None:
        raise HTTPException(
            422, f"Supply at most one of `{field_name}_file`/`{field_name}_asset_id`."
        )
    if asset_id is not None:
        asset = library.get(asset_id)
        if asset is None:
            raise HTTPException(422, f"No asset with id '{asset_id}'")
        return asset.path
    if upload is not None:
        path = job_dir / (upload.filename or f"{field_name}.wav")
        await _save_upload(upload, path)
        return path
    return None


_FRONTEND_NOT_BUILT_HTML = """<!doctype html>
<title>cinemagraph-tool</title>
<h1>The web UI hasn't been built yet</h1>
<p>The API itself is running fine -- every other route works. This page is the
built <code>frontend/</code> bundle, which isn't where the server expects it:</p>
<pre>{dist_dir}</pre>
<p>Build it with <code>cd frontend &amp;&amp; bun install &amp;&amp; bun run build</code>, or point
<code>CINEMAGRAPH_FRONTEND_DIR</code> at an existing build. For frontend development
run <code>bun run dev</code> instead and use its own server (port 5173), which
proxies API calls back here.</p>
"""


def _frontend_index(settings: Settings) -> Response:
    """The SPA's entry document, or a page explaining how to build it.

    Deliberately not a hard failure: an API-only deployment (or a source
    checkout where nobody has run `bun run build`) still starts and serves
    every other route, exactly like an unconfigured optional service degrades
    to a clear message rather than a 500. The old single-page UI couldn't have
    this problem -- it was a Python string -- so this is the cost of moving to
    a real bundle, paid once, here.
    """
    index_file = settings.frontend_dist_dir / "index.html"
    if not index_file.is_file():
        return HTMLResponse(
            _FRONTEND_NOT_BUILT_HTML.format(dist_dir=settings.frontend_dist_dir),
            status_code=503,
        )
    # no-store: index.html names hash-suffixed asset files, so a cached copy
    # of it keeps pointing at bundles that no longer exist after a rebuild.
    # The assets themselves are content-hashed and cached hard (see below).
    return FileResponse(index_file, headers={"cache-control": "no-store"})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(settings: SettingsDep):
    """The web UI: `frontend/`'s built SPA (see that directory's README and
    docs/DESIGN.md sec 5.5). Replaced server/ui.py's inline HTML string in
    2026-08-14 once the UI outgrew a single page.
    """
    return _frontend_index(settings)


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def frontend_spa_root(settings: SettingsDep):
    """The SPA's own entry point. `/` redirects here client-side."""
    return _frontend_index(settings)


@app.get("/ui/{spa_path:path}", response_class=HTMLResponse, include_in_schema=False)
async def frontend_spa(spa_path: str, settings: SettingsDep):
    """Every client-side route (/ui/library, /ui/music, ...) serves the same
    document -- the router picks the view once it's running -- so a hard
    reload or a shared link works rather than 404ing.

    The catch-all is scoped to /ui rather than mounted at the root precisely
    because this API owns unprefixed top-level paths (/library, /assemble,
    /jobs, ...): a root-level SPA fallback would shadow them, the same
    collision the frontend's own /ui namespace exists to avoid.
    """
    del spa_path  # every path under /ui resolves to the same entry document
    return _frontend_index(settings)


@app.get("/assets/{asset_path:path}", include_in_schema=False)
async def frontend_asset(asset_path: str, settings: SettingsDep):
    """Vite emits every bundle under dist/assets with a content hash in its
    name, so these are immutable and cached accordingly.

    Served through a route rather than `app.mount(StaticFiles(...))` because a
    mount resolves its directory once at import time, which would put the
    frontend path back into the "config frozen at import" category config.py
    exists to get out of -- and would make it un-overridable in tests.
    """
    root = settings.frontend_dist_dir / "assets"
    requested = (root / asset_path).resolve()
    # `..` in the URL path must not escape the bundle directory. FastAPI
    # already normalises most of it away, but this is the check that makes
    # that a guarantee rather than a trust in the framework's parsing.
    if not requested.is_relative_to(root) or not requested.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        requested, headers={"cache-control": "public, max-age=31536000, immutable"}
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(settings: SettingsDep):
    """image_generation/music_generation are each true if *either* their
    self-hosted satellite health-checks (a real, fresh-per-request call, same
    as every other satellite) or Gemini/Lyria3 is registered (no equivalent
    health check exists for a hosted API -- its registration, gated on
    GEMINI_API_KEY at app startup in this module, is the only signal
    available). *_models lists every registered adapter regardless of live
    reachability -- same "checked fresh at actual request time" philosophy as
    the rest of this file: a registered-but-currently-unreachable SDXL/
    ACE-Step still shows up here, and a real generate request against it
    still degrades to a clear per-job error, exactly as before either
    adapter existed.

    `configured` distinguishes "nothing to hint about" from "operator
    likely just forgot to start a container": a satellite whose env var is
    set but whose health check currently fails is `configured=True,
    <bool>=False` -- the web UI uses that combination to show a hint instead
    of hiding the tab outright (see frontend/src/lib/tabs.ts).
    image_generation's/
    music_generation's own `configured` is true if either backend has *any*
    config present (URL or key), independent of live reachability --
    deliberately not narrowed to "SDXL/ACE-Step only", since a Gemini key
    alone is enough to make either capability genuinely configured even with
    no self-hosted URL at all.

    `music_remix_models` is a subset of `music_generation_models` -- only
    adapters whose `remix()` actually does something (ACEStepAdapter;
    Lyria3Adapter's raises NotImplementedError, see its own docstring). The
    web UI's model picker filters to this list whenever a remix is being
    requested, so a caller can't pick a non-remix-capable model through the
    UI -- POST /generate/music still rejects it server-side too, since the
    UI filtering isn't the only way to hit this route.
    """
    return CapabilitiesResponse(
        semantic_mask=await service_available(settings.semantic_mask_service),
        music_generation=(
            await service_available(settings.music_service)
            or bool(settings.gemini_api_key)
        ),
        sound_effect_generation=await service_available(settings.sound_effect_service),
        image_generation=(
            await service_available(settings.image_generation_service)
            or bool(settings.gemini_api_key)
        ),
        image_generation_models=list(available_image_generators()),
        music_generation_models=list(available_music_generators()),
        music_remix_models=list(available_music_remix_generators()),
        configured={
            "semantic_mask": bool(settings.ml_service_url),
            "music_generation": bool(settings.acestep_url)
            or bool(settings.gemini_api_key),
            "sound_effect_generation": bool(settings.sound_effects_url),
            "image_generation": bool(settings.image_generation_url)
            or bool(settings.gemini_api_key),
        },
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
        service.run_render_job,
        job.id,
        pipeline.save_cinemagraph_video,
        output_path,
        library_kind="generated",
        library_tags=["video"],
        input_path=str(input_path),
        mask_path=str(mask_path) if mask_path else None,
        still_frame_index=still_frame_index,
        blend_frames=blend_frames,
        auto_trim_loop=auto_trim,
        mask_threshold=mask_threshold,
        feather=feather,
        apply_grade=apply_grade,
        grade_strength=grade_strength,
        grain=grain,
        also_gif=also_gif,
        loop_duration=loop_duration,
    )
    return JobResponse(job_id=job.id)


@app.post("/render/photo", response_model=JobResponse)
async def render_photo(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    input_file: UploadFile | None = File(None),
    input_asset_id: str | None = Form(None),
    effect: list[str] = Form(...),
    mask: UploadFile | None = File(None),
    mask_prompt: str | None = Form(None),
    unmasked_effects: list[str] = Form([]),
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

    Supply exactly one of `input_file` (a fresh upload) or `input_asset_id`
    (an existing library asset's id, resolved via `asset_library.get()` --
    its stored file is used directly, no upload/copy needed). Supply at most
    one of `mask` (a hand-painted mask file, same convention as the CLI's
    --mask) or `mask_prompt` (e.g. "clouds", "sun" -- segments the photo via
    the machine-learning service first and renders with that mask; the job
    errors with a 503-style message if that service isn't
    configured/reachable, rather than silently rendering unmasked).
    `unmasked_effects` opts specific requested effects out of the mask
    entirely, so e.g. snow can animate over the whole photo while flicker
    stays confined to `mask`/`mask_prompt` -- see animate_photo()'s own
    docstring. Per-effect overrides (rain_count, ripple_amplitude, etc.)
    mirror the CLI's per-effect flags one-for-one and go through the same
    validation.resolve_effect_kwargs check, so an override for an effect
    that wasn't requested is a 422 here exactly as it's a click.UsageError
    on the CLI.
    """
    unknown = [e for e in effect if e not in effects_pkg.EFFECTS]
    if unknown:
        raise HTTPException(
            422,
            f"Unknown effect(s) {unknown}. Choose from: {', '.join(effects_pkg.EFFECTS)}",
        )
    if mask is not None and mask_prompt:
        raise HTTPException(422, "Supply either `mask` or `mask_prompt`, not both.")
    if loop_duration and also_gif:
        raise HTTPException(422, pipeline._LOOP_DURATION_GIF_ERROR)
    if (input_file is None) == (input_asset_id is None):
        raise HTTPException(
            422, "Supply exactly one of `input_file` or `input_asset_id`."
        )

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
        resolved_unmasked = validation.resolve_unmasked_effects(
            effect, unmasked_effects
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    if input_asset_id is not None:
        asset = library.get(input_asset_id)
        if asset is None:
            raise HTTPException(422, f"No asset with id '{input_asset_id}'")
        input_path = asset.path
    else:
        input_path = job_dir / (input_file.filename or "input")
        await _save_upload(input_file, input_path)
    output_path = job_dir / "output.mp4"

    render_kwargs = dict(
        effect=effect,
        duration=duration,
        fps=fps,
        speed=speed,
        feather=feather,
        apply_grade=apply_grade,
        grade_strength=grade_strength,
        grain=grain,
        also_gif=also_gif,
        effect_kwargs=effect_kwargs,
        unmasked_effects=resolved_unmasked,
        loop_duration=loop_duration,
    )

    if mask_prompt:
        background_tasks.add_task(
            service.run_photo_semantic_mask_job,
            job.id,
            output_path,
            settings=settings,
            photo_path=input_path,
            mask_path=job_dir / "mask.png",
            mask_prompt=mask_prompt,
            library_kind="generated",
            library_tags=["photo", *effect],
            **render_kwargs,
        )
    else:
        mask_path = None
        if mask is not None:
            mask_path = job_dir / (mask.filename or "mask.png")
            await _save_upload(mask, mask_path)
        background_tasks.add_task(
            service.run_render_job,
            job.id,
            pipeline.save_cinemagraph_from_photo,
            output_path,
            library_kind="generated",
            library_tags=["photo", *effect],
            photo_path=str(input_path),
            mask_path=str(mask_path) if mask_path else None,
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
        service.run_render_job,
        job.id,
        pipeline.save_mask_preview,
        output_path,
        input_path=str(input_path),
        mask_threshold=mask_threshold,
        feather=feather,
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
        can_save=job.pending_library is not None,
        saved_asset_id=job.saved_asset_id,
    )


@app.get("/jobs/{job_id}/file")
async def job_file(job_id: str):
    job = jobs.get_job(job_id)
    if job is None or job.status != jobs.JobStatus.DONE or job.output_path is None:
        raise HTTPException(404, "Output not available")
    return FileResponse(str(job.output_path))


@app.post("/jobs/{job_id}/save", response_model=AssetResponse)
async def save_job(job_id: str, project: str | None = Form(None)):
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


def _asset_to_response(asset) -> AssetResponse:
    project = next(
        (
            t[len(library.PROJECT_TAG_PREFIX) :]
            for t in asset.tags
            if t.startswith(library.PROJECT_TAG_PREFIX)
        ),
        None,
    )
    tags = [t for t in asset.tags if not t.startswith(library.PROJECT_TAG_PREFIX)]
    return AssetResponse(
        id=asset.id,
        kind=asset.kind,
        original_filename=asset.original_filename,
        added_at=asset.added_at,
        tags=tags,
        provenance=asset.provenance,
        project=project,
    )


@app.post("/library", response_model=AssetResponse)
async def library_add(
    upload: UploadFile = File(...),
    kind: str = Form("reference"),
    tags: str = Form(""),
    project: str | None = Form(None),
):
    if kind not in library.KINDS:
        raise HTTPException(
            422, f"Unknown kind '{kind}'. Choose from: {', '.join(library.KINDS)}"
        )

    with tempfile.NamedTemporaryFile(
        suffix=Path(upload.filename or "").suffix, delete=False
    ) as tmp:
        tmp.write(await upload.read())
        tmp_path = Path(tmp.name)
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if project:
            tag_list.append(f"{library.PROJECT_TAG_PREFIX}{project}")
        asset = library.add(
            str(tmp_path), kind=kind, tags=tag_list, original_filename=upload.filename
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    return _asset_to_response(asset)


@app.get("/library", response_model=list[AssetResponse])
async def library_list(
    kind: str | None = None, tag: str | None = None, project: str | None = None
):
    return [
        _asset_to_response(a)
        for a in library.list_assets(kind=kind, tag=tag, project=project)
    ]


@app.post("/library/{asset_id}/project", response_model=AssetResponse)
async def set_asset_project(asset_id: str, project: str | None = Form(None)):
    """Assigns (or clears, if `project` is empty/omitted) the project an
    existing library asset belongs to -- the Library tab's own way to
    organize/reorganize content after the fact, independent of the
    save-time assignment POST /jobs/{id}/save also offers."""
    try:
        asset = library.set_project(asset_id, project or None)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _asset_to_response(asset)


@app.get("/projects", response_model=list[str])
async def list_projects():
    return library.list_projects()


@app.post("/projects/rename")
async def rename_project(old: str = Form(...), new: str = Form(...)):
    count = library.rename_project(old, new)
    return {"renamed": old, "to": new, "count": count}


@app.delete("/projects/{name}")
async def delete_project(name: str):
    count = library.delete_project(name)
    return {"deleted": name, "count": count}


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
async def semantic_mask(
    settings: SettingsDep, image: UploadFile = File(...), prompt: str = Form(...)
):
    """Proxies to the machine-learning (CLIPSeg) service. Returns a clean 503,
    not a 500, whenever that service isn't configured or isn't reachable --
    see machine-learning/README.md for the /segment contract this proxies.
    """
    files = {"image": (image.filename, await image.read(), image.content_type)}
    resp = await call_optional_service(
        settings.semantic_mask_service,
        "POST",
        "/segment",
        data={"prompt": prompt},
        files=files,
        timeout=60.0,
    )
    return Response(content=resp.content, media_type="image/png")


_MUSIC_TASK_TYPES = ("text2music", "cover", "repaint")


@app.post("/generate/music", response_model=JobResponse)
async def generate_music(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    prompt: str = Form(""),
    lyrics: str = Form(""),
    duration: float = Form(30.0),
    thinking: bool = Form(True),
    instrumental: bool = Form(False),
    model: str = Form("acestep"),
    task_type: str = Form("text2music"),
    src_audio_file: UploadFile | None = File(None),
    src_audio_asset_id: str | None = Form(None),
    reference_audio_file: UploadFile | None = File(None),
    reference_audio_asset_id: str | None = Form(None),
    cover_strength: float = Form(1.0),
    repainting_start: float = Form(0.0),
    repainting_end: float = Form(-1.0),
):
    """Proxies to a registered MusicGenerator adapter (generation_ports.py) --
    `model` picks which one ("acestep" = local ACE-Step, self-hosted GPU
    queue; "lyria3" = Google's hosted Lyria 3, when GEMINI_API_KEY is
    configured). Same degrade-cleanly contract as /mask/semantic: reuses
    this file's own job system rather than adding new status-tracking
    routes -- GET /jobs/{job_id} and /jobs/{job_id}/file already work for
    this without any changes.

    `instrumental`'s exact effect depends on the chosen `model` -- see each
    adapter's own docstring (ACEStepAdapter substitutes a marker that
    reliably forces it; Lyria3Adapter only requests it via a prompt
    instruction, not a guaranteed override).

    `task_type="cover"`/`"repaint"` remix an existing song -- `src_audio_file`
    (a fresh upload) or `src_audio_asset_id` (an existing library asset),
    exactly one, required for either. `lego`/`extract`/`complete` are real
    ACE-Step task types but base-model-only -- rejected here since the
    currently deployed model is acestep-v15-turbo, a deployment fact this
    route can't work around. `reference_audio_file`/`reference_audio_asset_id`
    (style transfer) is independent of `task_type` and always optional.
    `cover_strength` only matters for `cover`; `repainting_start`/
    `repainting_end` only for `repaint` (`repainting_end=-1` means to the end
    of the source audio) -- see ACEStepAdapter.remix()'s own docstring.
    """
    if task_type not in _MUSIC_TASK_TYPES:
        raise HTTPException(
            422,
            f"Unknown or unsupported task_type '{task_type}'. Choose from: {', '.join(_MUSIC_TASK_TYPES)} "
            "(lego/extract/complete are base-model-only, not supported by the current deployment).",
        )
    is_remix = (
        task_type != "text2music"
        or reference_audio_file is not None
        or reference_audio_asset_id is not None
    )
    valid_models = (
        available_music_remix_generators() if is_remix else available_music_generators()
    )
    if model not in valid_models:
        raise HTTPException(
            422,
            f"Unknown model '{model}' for this request. Choose from: {', '.join(valid_models)}",
        )
    if (
        task_type in ("cover", "repaint")
        and src_audio_file is None
        and src_audio_asset_id is None
    ):
        raise HTTPException(
            422,
            f"task_type='{task_type}' needs `src_audio_file` or `src_audio_asset_id`.",
        )

    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.mp3"

    src_audio_path = await _resolve_optional_audio_input(
        src_audio_file, src_audio_asset_id, job_dir, "src_audio"
    )
    reference_audio_path = await _resolve_optional_audio_input(
        reference_audio_file, reference_audio_asset_id, job_dir, "reference_audio"
    )

    background_tasks.add_task(
        service.run_music_job,
        job.id,
        output_path,
        settings=settings,
        prompt=prompt,
        lyrics=lyrics,
        duration=duration,
        thinking=thinking,
        instrumental=instrumental,
        model=model,
        task_type=task_type,
        src_audio_path=src_audio_path,
        reference_audio_path=reference_audio_path,
        cover_strength=cover_strength,
        repainting_start=repainting_start,
        repainting_end=repainting_end,
        library_kind="generated",
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
        service.run_sound_effect_job,
        job.id,
        output_path,
        settings=settings,
        prompt=prompt,
        duration=duration,
        library_kind="generated",
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
            422,
            f"Unknown model '{model}'. Choose from: {', '.join(available_image_generators())}",
        )

    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.png"

    reference_image_path = None
    if reference_image is not None:
        reference_image_path = job_dir / (reference_image.filename or "reference.png")
        await _save_upload(reference_image, reference_image_path)

    background_tasks.add_task(
        service.run_image_job,
        job.id,
        output_path,
        settings=settings,
        prompt=prompt,
        reference_image_path=reference_image_path,
        strength=strength,
        model=model,
        library_kind="generated",
    )
    return JobResponse(job_id=job.id)


def _resolve_asset_paths(asset_ids: list[str]) -> list[str]:
    """Resolves each id via asset_library.get(), raising a 422-shaped
    HTTPException naming the first missing one -- same eager-validation-at-
    the-route style /render/photo already uses for unknown effect names,
    rather than letting a bad id surface as an opaque failure deep inside
    the background job."""
    paths = []
    for asset_id in asset_ids:
        asset = library.get(asset_id)
        if asset is None:
            raise HTTPException(422, f"No asset with id '{asset_id}'")
        paths.append(str(asset.path))
    return paths


@app.post("/assemble", response_model=JobResponse)
async def assemble(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    clip_asset_ids: list[str] = Form(...),
    music_asset_ids: list[str] = Form(...),
    sound_effect_asset_ids: list[str] = Form([]),
    video_crossfade_duration: float = Form(1.0),
    music_crossfade_duration: float = Form(5.0),
    music_edge_fade_duration: float = Form(2.0),
    music_gap_duration: float = Form(0.0),
):
    """Combines already-generated library assets (clips, music, sound
    effects) into one finished video via assembly.pipeline.assemble --
    crossfades between clips, crossfades between songs plus a fade-in/out at
    the whole track's edges, sound effects layered continuously under the
    music. `music_gap_duration > 0` replaces the crossfade between songs
    with real silence instead (mutually exclusive with
    `music_crossfade_duration`) -- sound effects still play continuously
    through it, only the music pauses. See run_assembly_job's and
    audio_track.build_music_track's own docstrings, and docs/DESIGN.md sec
    5.6.

    Every input is a library asset id (not an upload) -- assembly consumes
    "generated resources" that already exist, it doesn't generate anything
    new itself. Runs as a background job purely because ffmpeg's own work
    takes real time, same as every other multi-second operation here --
    no external HTTP call is involved, unlike /generate/music's own
    async job (see run_assembly_job's own async-vs-sync note).
    """
    video_clip_paths = _resolve_asset_paths(clip_asset_ids)
    music_track_paths = _resolve_asset_paths(music_asset_ids)
    sound_effect_paths = (
        _resolve_asset_paths(sound_effect_asset_ids) if sound_effect_asset_ids else None
    )

    job = jobs.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.mp4"

    background_tasks.add_task(
        service.run_assembly_job,
        job.id,
        output_path,
        video_clip_paths=video_clip_paths,
        music_track_paths=music_track_paths,
        sound_effect_paths=sound_effect_paths,
        video_crossfade_duration=video_crossfade_duration,
        music_crossfade_duration=music_crossfade_duration,
        music_edge_fade_duration=music_edge_fade_duration,
        music_gap_duration=music_gap_duration,
        library_kind="generated",
        provenance={
            "clip_asset_ids": clip_asset_ids,
            "music_asset_ids": music_asset_ids,
            "sound_effect_asset_ids": sound_effect_asset_ids,
            "video_crossfade_duration": video_crossfade_duration,
            "music_crossfade_duration": music_crossfade_duration,
            "music_edge_fade_duration": music_edge_fade_duration,
            "music_gap_duration": music_gap_duration,
        },
    )
    return JobResponse(job_id=job.id)
