"""Business workflows behind the API's routes.

Everything here answers "what work does this request actually perform",
leaving app.py to do nothing but parse a request, hand off, and shape a
response -- the router/service split that's standard for FastAPI projects
of any size (see docs/DESIGN.md's decision log).

Two concrete payoffs beyond tidiness:

- These functions are unit-testable on their own. (ACE-Step's own polling
  loop -- remote failure, timeout -- now lives in generation_adapters.py's
  ACEStepAdapter, tested there directly; run_music_job's own tests just
  confirm it delegates to an injected MusicGenerator correctly.)
- This is the one module that knows about *both* the external services and
  the rendering pipeline. Neither knows about the other: cinemagraph/ never
  learns that HTTP exists, _external_service.py never learns that renders
  exist. Composition happens here and only here.

Async vs sync matters here: FastAPI runs a *sync* BackgroundTask in a
threadpool, but an *async* one on the event loop. So any coroutine below
that also does blocking CPU work (rendering) must push that work off the
loop with asyncio.to_thread, or it stalls every other request for the
duration of the render.

The decision rule that follows from that, for any job added here: stay
plain `def` unless the job has a genuine await-worthy operation (real
network I/O). Plain `def` gets Starlette's threadpool dispatch
unconditionally -- there's no way to write it wrong. `async def` with
nothing left to genuinely await is strictly worse: same runtime outcome if
done correctly, but now depends on remembering to wrap every blocking call
in asyncio.to_thread, with no safety net if that's missed -- it would
compile, run fine in casual testing, and silently freeze the whole server
the first time the gap is actually hit. run_render_job (no external calls)
is sync; the other three (each makes a real HTTP call) are async. If a
future change ever removes a job's last genuine await, it should collapse
back to plain def rather than keep an async signature that no longer
describes anything real about it.

Config arrives as an explicit `settings` argument rather than being read
here. These run as BackgroundTasks, outside the request/response cycle, so
FastAPI's Depends() cannot inject into them -- the route resolves settings
(where injection *does* work) and passes them through. That keeps this
module free of any environment lookups, which is also why its tests can
construct a Settings() directly instead of monkeypatching os.environ.

External services are dependency-injected the same way run_render_job
already injects render_fn -- an explicit parameter each job takes rather
than calling a name directly via module import, so tests pass a fake in as
an argument instead of monkeypatching this module's namespace. Two shapes
of that seam exist here now:

- run_photo_semantic_mask_job (the one job with no dedicated port/adapter of
  its own -- semantic masking has never lived through a second backend, so
  there's nothing yet to justify one per §3.3's own bar) takes `call_service`
  (defaulting to call_optional_service) and `render_fn` (defaulting to
  pipeline.save_cinemagraph_from_photo) directly.
- run_image_job/run_music_job/run_sound_effect_job each take an injected
  port object (ImageGenerator/MusicGenerator/SoundEffectGenerator,
  generation_ports.py) instead -- one level of abstraction higher (a whole
  capability, not one bare function). run_image_job's default comes from
  generation_registry.get_image_generator(model): a real second adapter
  (GeminiAdapter, alongside SDXLAdapter) exists, so a per-request `model`
  field lets a caller pick which one, the entire reason the registry (not
  just a single swappable reference) exists per §3.6. run_music_job/
  run_sound_effect_job default to a freshly-built ACEStepAdapter(settings)/
  StableAudioAdapter(settings) instead -- registered in generation_registry
  for mechanism-completeness, but not consulted by name yet, since no
  second music/sound-effect backend exists to justify a `model` field
  (image generation went through this identical single-adapter phase before
  Gemini existed). For real requests using the registry is safe regardless:
  app.py's routes resolve `settings` via Depends(get_settings), the exact
  cached singleton every registered adapter was itself built from at
  app-import time, so there's no divergence from a test's own custom
  Settings(...) -- tests that want to fake the HTTP/API layer inject the
  port parameter directly, bypassing lookup entirely either way. See
  docs/DESIGN.md sec 3.6 for the full rationale and history.

Library registration: every job function below takes an optional
`library_kind` -- when given (the four /render and /generate routes all
pass "generated"; /mask-preview does not, since a diagnostic mask preview
isn't an asset worth cataloging), the finished output is registered via
asset_library.add() right after jobs.mark_done. That call is best-effort
and deliberately never allowed to flip a job to "error": the render/generate
itself already succeeded and its file already exists, so a cataloging
hiccup (e.g. the library's own storage location being unwritable) is a
real but separate failure that shouldn't hide a working result from the
caller. Only source files (the render/generate output) are auto-registered,
not uploaded inputs -- unlike a deliberate `cinemagraph library add`, every
upload passing through this API is not necessarily something the user
wants kept forever.
"""
import asyncio
from pathlib import Path

import asset_library
from fastapi import HTTPException

from assembly import ffmpeg_runner as assembly_ffmpeg_runner
from assembly import pipeline as assembly_pipeline
from cinemagraph import pipeline

from . import jobs
from ._external_service import call_optional_service
from .config import Settings
from .generation_adapters import ACEStepAdapter, StableAudioAdapter
from .generation_ports import ImageGenerator, MusicGenerator, SoundEffectGenerator
from .generation_registry import get_image_generator


def _register_in_library(output_path: Path, kind: str | None, tags: list[str] | None, provenance: dict | None) -> None:
    if kind is None:
        return
    try:
        asset_library.add(str(output_path), kind=kind, tags=tags or [], provenance=provenance)
    except Exception:
        pass  # cataloging is best-effort; the render/generate already succeeded


def run_render_job(
    job_id: str, render_fn, output_path: Path, *,
    library_kind: str | None = None, library_tags: list[str] | None = None, **kwargs,
) -> None:
    """Sync (threadpool) job: a purely local render, no external service."""
    jobs.mark_running(job_id)
    try:
        render_fn(output_path=str(output_path), **kwargs)
    except Exception as e:
        jobs.mark_error(job_id, str(e))
        return
    jobs.mark_done(job_id, output_path)
    _register_in_library(output_path, library_kind, library_tags, provenance=None)


async def run_photo_semantic_mask_job(
    job_id: str,
    output_path: Path,
    *,
    settings: Settings,
    photo_path: Path,
    mask_path: Path,
    mask_prompt: str,
    library_kind: str | None = None,
    library_tags: list[str] | None = None,
    call_service=call_optional_service,
    render_fn=pipeline.save_cinemagraph_from_photo,
    **render_kwargs,
) -> None:
    """Segment the photo by text prompt, then render using that mask.

    The chained equivalent of doing POST /mask/semantic by hand and feeding
    the resulting PNG to a render as --mask. Works because the machine-learning
    service returns exactly the format cinemagraph.mask.load_mask() already
    expects (grayscale PNG, white = animate) -- no conversion step needed.

    The mask PNG is kept in the job directory rather than discarded, so a
    disappointing result can be inspected to tell "the mask was wrong" apart
    from "the effect was wrong".
    """
    jobs.mark_running(job_id)
    try:
        image_bytes = photo_path.read_bytes()
        resp = await call_service(
            settings.semantic_mask_service, "POST", "/segment",
            data={"prompt": mask_prompt},
            files={"image": (photo_path.name, image_bytes, "application/octet-stream")},
            timeout=60.0,
        )
        mask_path.write_bytes(resp.content)

        await asyncio.to_thread(
            render_fn,
            photo_path=str(photo_path),
            output_path=str(output_path),
            mask_path=str(mask_path),
            **render_kwargs,
        )
        jobs.mark_done(job_id, output_path)
        _register_in_library(
            output_path, library_kind, library_tags,
            provenance={"mask_prompt": mask_prompt, **render_kwargs},
        )
    except HTTPException as e:
        jobs.mark_error(job_id, e.detail)
    except Exception as e:
        jobs.mark_error(job_id, str(e))


async def run_music_job(
    job_id: str,
    output_path: Path,
    *,
    settings: Settings,
    prompt: str,
    lyrics: str,
    duration: float,
    thinking: bool,
    instrumental: bool = False,
    library_kind: str | None = None,
    music_generator: MusicGenerator | None = None,
) -> None:
    """Proxies to a MusicGenerator adapter (generation_ports.py), defaulting
    to ACEStepAdapter (generation_adapters.py) -- same DI shape run_image_job
    already uses, extracted once image generation had proven the pattern
    twice over (see docs/DESIGN.md sec 3.6). Only "acestep" exists as a
    backend today, so unlike run_image_job there's no `model` field yet.

    Provenance records the *lyrics actually submitted*, not whatever
    backend-internal marker an adapter substitutes for `instrumental=True`
    (ACEStepAdapter's own docstring explains why that substitution is its
    concern, not this function's).
    """
    jobs.mark_running(job_id)
    try:
        generator = music_generator or ACEStepAdapter(settings)
        audio_bytes = await generator.generate(
            prompt, lyrics=lyrics, duration=duration, thinking=thinking, instrumental=instrumental,
        )
        output_path.write_bytes(audio_bytes)
        jobs.mark_done(job_id, output_path)
        _register_in_library(
            output_path, library_kind, tags=["music"],
            provenance={
                "prompt": prompt, "lyrics": lyrics,
                "duration": duration, "thinking": thinking, "instrumental": instrumental,
            },
        )
    except HTTPException as e:
        jobs.mark_error(job_id, e.detail)
    except Exception as e:
        jobs.mark_error(job_id, str(e))


async def run_sound_effect_job(
    job_id: str, output_path: Path, *, settings: Settings, prompt: str, duration: float,
    library_kind: str | None = None,
    sound_effect_generator: SoundEffectGenerator | None = None,
) -> None:
    """Proxies to a SoundEffectGenerator adapter (generation_ports.py),
    defaulting to StableAudioAdapter -- same DI shape/history as
    run_music_job above.
    """
    jobs.mark_running(job_id)
    try:
        generator = sound_effect_generator or StableAudioAdapter(settings)
        audio_bytes = await generator.generate(prompt, duration=duration)
        output_path.write_bytes(audio_bytes)
        jobs.mark_done(job_id, output_path)
        _register_in_library(
            output_path, library_kind, tags=["sound-effect"],
            provenance={"prompt": prompt, "duration": duration},
        )
    except HTTPException as e:
        jobs.mark_error(job_id, e.detail)
    except Exception as e:
        jobs.mark_error(job_id, str(e))


async def run_image_job(
    job_id: str, output_path: Path, *, settings: Settings, prompt: str,
    reference_image_path: Path | None = None,
    strength: float = 0.6,
    model: str = "sdxl",
    library_kind: str | None = None,
    image_generator: ImageGenerator | None = None,
) -> None:
    """Proxies to an ImageGenerator adapter (generation_ports.py) -- `model`
    selects which registered adapter to use ("sdxl" = local SDXL,
    image-generation/; "gemini" = Google's hosted API, generation/), each
    with its own tradeoffs: SDXL is free and local but quality-gapped against
    frontier hosted models; Gemini needs a paid API key but has no local
    VRAM ceiling. See docs/experiments/ for the account of both, and
    docs/DESIGN.md sec 3.6 for why that history is what justifies the
    ImageGenerator port/registry.

    `reference_image_path`, when given, switches the adapter into img2img
    mode. `strength` (0=stay close to the reference, 1=ignore it) only
    matters for SDXL -- GeminiAdapter accepts and ignores it (Gemini's own
    API has no equivalent knob).
    """
    jobs.mark_running(job_id)
    try:
        generator = image_generator or get_image_generator(model)
        reference_image_bytes = None
        reference_image_filename = "reference.png"
        if reference_image_path is not None:
            reference_image_bytes = reference_image_path.read_bytes()
            reference_image_filename = reference_image_path.name

        image_bytes = await generator.generate(
            prompt,
            reference_image_bytes=reference_image_bytes,
            reference_image_filename=reference_image_filename,
            strength=strength,
        )
        output_path.write_bytes(image_bytes)
        jobs.mark_done(job_id, output_path)
        provenance = {"prompt": prompt, "model": model}
        if reference_image_path is not None:
            provenance["strength"] = strength
        _register_in_library(
            output_path, library_kind, tags=["image"],
            provenance=provenance,
        )
    except HTTPException as e:
        jobs.mark_error(job_id, e.detail)
    except Exception as e:
        jobs.mark_error(job_id, str(e))


def run_assembly_job(
    job_id: str, output_path: Path, *,
    video_clip_paths: list[str], music_track_paths: list[str],
    sound_effect_paths: list[str] | None = None,
    video_crossfade_duration: float = 1.0,
    music_crossfade_duration: float = 2.0,
    music_edge_fade_duration: float = 2.0,
    music_gap_duration: float = 0.0,
    library_kind: str | None = None,
    provenance: dict | None = None,
    run_ffmpeg=assembly_ffmpeg_runner.run_ffmpeg,
) -> None:
    """Sync (threadpool) job: local ffmpeg subprocess work (assembly.pipeline
    .assemble), no external HTTP call -- same shape as run_render_job, per
    this module's own async-vs-sync rule (stay plain def unless there's a
    genuine await-worthy operation). See docs/DESIGN.md sec 5.6.

    `provenance` is built by the caller (app.py's route, which already
    resolved whatever asset IDs were given to file paths) rather than by
    this function -- unlike the generation jobs above, the meaningful
    identifying info here is which *assets* were combined, not a handful of
    scalar params this function itself owns.
    """
    jobs.mark_running(job_id)
    try:
        assembly_pipeline.assemble(
            video_clip_paths=video_clip_paths,
            music_track_paths=music_track_paths,
            output_path=str(output_path),
            sound_effect_paths=sound_effect_paths,
            video_crossfade_duration=video_crossfade_duration,
            music_crossfade_duration=music_crossfade_duration,
            music_edge_fade_duration=music_edge_fade_duration,
            music_gap_duration=music_gap_duration,
            run_ffmpeg=run_ffmpeg,
        )
    except Exception as e:
        jobs.mark_error(job_id, str(e))
        return
    jobs.mark_done(job_id, output_path)
    _register_in_library(output_path, library_kind, ["assembled"], provenance=provenance)
