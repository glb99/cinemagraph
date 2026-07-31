"""Business workflows behind the API's routes.

Everything here answers "what work does this request actually perform",
leaving app.py to do nothing but parse a request, hand off, and shape a
response -- the router/service split that's standard for FastAPI projects
of any size (see docs/DESIGN.md's decision log).

Two concrete payoffs beyond tidiness:

- These functions are unit-testable on their own. The polling branches in
  run_music_job (remote failure, timeout) are otherwise only reachable by
  driving a whole HTTP round-trip, which is why they went untested while
  this lived in app.py.
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

External-service calls and the actual render are dependency-injected the
same way run_render_job already injects render_fn: each async job takes an
explicit `call_service` parameter (defaulting to the real
call_optional_service), and run_photo_semantic_mask_job additionally takes
`render_fn` (defaulting to pipeline.save_cinemagraph_from_photo), rather
than calling either name directly via module import. Tests pass a fake in
as an argument instead of monkeypatching this module's namespace -- the
seam is the same one either way (both are genuinely the one function that
crosses an I/O boundary here, network for one and disk/CPU-bound render
for the other), this just makes it an explicit part of each function's
signature instead of an implicit one only reachable by patching internals.
Neither parameter ever varies across real callers today -- app.py never
passes anything but the default for either -- so the justification isn't
"production polymorphism," it's that an explicit parameter is a more
honest, refactor-safe seam than patching a module attribute (monkeypatch
has to guess the exact name a call site looked up, which silently breaks
if an import is ever restructured; an injected parameter can't).

run_image_job depends on an injected ImageGenerator (generation_ports.py)
rather than building the image-generation/ request inline -- the same
DI shape call_service=/render_fn= already use elsewhere in this file, one
level higher (a whole capability, not one bare function). Now that a real
second adapter exists (GeminiAdapter, alongside SDXLAdapter), the default
comes from generation_registry.get_image_generator(model) -- the per-request
`model` field lets a caller pick which registered adapter to use, which is
the entire reason the registry (not just a single swappable reference)
exists per §3.6. For real requests this is safe: app.py's route resolves
`settings` via Depends(get_settings), the exact cached singleton the
registry's adapters were themselves built from at app-import time, so
there's no divergence. Tests that want to fake the HTTP/API layer inject
`image_generator=` directly (bypassing model lookup entirely), same as
before -- that seam doesn't care where the default would have come from.
See docs/DESIGN.md sec 3.6 for the full rationale (this existed as one
hardcoded call before the refactor; image generation already lived through
two real backends -- Gemini's hosted API, then local SDXL, then Gemini again
as a coexisting adapter -- which is what justifies the port and registry).

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
import json
from pathlib import Path

import asset_library
from fastapi import HTTPException

from cinemagraph import pipeline

from . import jobs
from ._external_service import call_optional_service
from .config import Settings
from .generation_ports import ImageGenerator
from .generation_registry import get_image_generator

MUSIC_POLL_INTERVAL_SECONDS = 2.0
MUSIC_POLL_MAX_ATTEMPTS = 150  # ~5 minutes total


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
    call_service=call_optional_service,
) -> None:
    """Drives ACE-Step's own job-queue API to completion: create a task,
    poll until it reports success/failure, download the resulting audio.
    ACE-Step's response envelope wraps everything in {"data": ..., "code": ...};
    see docs/DESIGN.md / the acestep-docs skill's API.md for the full contract.

    ACE-Step's /release_task has no separate "instrumental" field (that only
    exists on its higher-level OpenRouter-compatible wrapper, a different API
    surface than the one this client talks to) -- its own server derives
    instrumental mode purely from the *lyrics string itself*
    (`acestep/api/server_utils.py`'s `is_instrumental`: true iff the
    stripped/lowercased lyrics equal "[inst]" or "[instrumental]"). An empty
    lyrics field does NOT trigger it -- ACE-Step will still attempt vocals.
    `instrumental=True` here overrides whatever lyrics text was provided with
    that exact marker, mirroring what ACE-Step's own Gradio UI's
    "Instrumental" checkbox does.
    """
    jobs.mark_running(job_id)
    service = settings.music_service
    effective_lyrics = "[Instrumental]" if instrumental else lyrics
    try:
        create_resp = await call_service(
            service, "POST", "/release_task",
            json={
                "prompt": prompt, "lyrics": effective_lyrics,
                "audio_duration": duration, "thinking": thinking,
            },
        )
        task_id = create_resp.json()["data"]["task_id"]

        result = None
        for _ in range(MUSIC_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(MUSIC_POLL_INTERVAL_SECONDS)
            # timeout=90 (call_optional_service's default is 30): found via a
            # real failure -- on a cold/just-restarted ACE-Step instance, its
            # own model-loading work blocks a single /query_result call
            # synchronously for the full duration of loading + first
            # generation, not just returning a quick "still running" status
            # the way it does once warm. 30s wasn't enough margin for that;
            # steady-state polling (the overwhelmingly common case) is
            # unaffected since those calls return almost immediately either way.
            query_resp = await call_service(
                service, "POST", "/query_result",
                json={"task_id_list": [task_id]}, timeout=90.0,
            )
            entry = query_resp.json()["data"][0]
            if entry["status"] == 1:
                result = json.loads(entry["result"])[0]
                break
            if entry["status"] == 2:
                raise RuntimeError("ACE-Step reported generation failure.")
        if result is None:
            raise RuntimeError(f"ACE-Step generation timed out after {MUSIC_POLL_MAX_ATTEMPTS} polls.")

        audio_resp = await call_service(service, "GET", result["file"], timeout=60.0)
        output_path.write_bytes(audio_resp.content)
        jobs.mark_done(job_id, output_path)
        _register_in_library(
            output_path, library_kind, tags=["music"],
            provenance={
                "prompt": prompt, "lyrics": effective_lyrics,
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
    call_service=call_optional_service,
) -> None:
    """Unlike ACE-Step's queue, sound-effects/ answers in one synchronous
    call -- still a background job here because generation takes real time.
    """
    jobs.mark_running(job_id)
    try:
        resp = await call_service(
            settings.sound_effect_service, "POST", "/generate",
            json={"prompt": prompt, "duration": duration}, timeout=300.0,
        )
        output_path.write_bytes(resp.content)
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
