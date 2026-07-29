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

run_image_job follows the exact same shape as run_sound_effect_job --
proxies to a local, self-hosted service (image-generation/, SDXL) over
call_optional_service, degrading the same way the others do when
IMAGE_GENERATION_URL isn't configured or the service isn't reachable. A
hosted API (Gemini's native image models) was tried first; see this
function's own docstring for why that was reverted.

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
    library_kind: str | None = None,
    call_service=call_optional_service,
) -> None:
    """Drives ACE-Step's own job-queue API to completion: create a task,
    poll until it reports success/failure, download the resulting audio.
    ACE-Step's response envelope wraps everything in {"data": ..., "code": ...};
    see docs/DESIGN.md / the acestep-docs skill's API.md for the full contract.
    """
    jobs.mark_running(job_id)
    service = settings.music_service
    try:
        create_resp = await call_service(
            service, "POST", "/release_task",
            json={"prompt": prompt, "lyrics": lyrics, "audio_duration": duration, "thinking": thinking},
        )
        task_id = create_resp.json()["data"]["task_id"]

        result = None
        for _ in range(MUSIC_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(MUSIC_POLL_INTERVAL_SECONDS)
            query_resp = await call_service(
                service, "POST", "/query_result",
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

        audio_resp = await call_service(service, "GET", result["file"], timeout=60.0)
        output_path.write_bytes(audio_resp.content)
        jobs.mark_done(job_id, output_path)
        _register_in_library(
            output_path, library_kind, tags=["music"],
            provenance={"prompt": prompt, "lyrics": lyrics, "duration": duration, "thinking": thinking},
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
    library_kind: str | None = None,
    call_service=call_optional_service,
) -> None:
    """Proxies to the image-generation/ service (local SDXL). Same shape as
    run_sound_effect_job -- a hosted API (Gemini's native image models) was
    tried first and reverted: new Google AI Studio accounts require a
    non-refundable minimum prepay to use it at all, discovered only by
    actually trying to generate an image, not from reading pricing docs.
    Local SDXL avoids that entirely, at the cost of a quality gap against
    frontier hosted models -- an accepted tradeoff given the billing friction.
    See docs/experiments/ for the full account of both attempts.
    """
    jobs.mark_running(job_id)
    try:
        resp = await call_service(
            settings.image_generation_service, "POST", "/generate",
            json={"prompt": prompt}, timeout=120.0,
        )
        output_path.write_bytes(resp.content)
        jobs.mark_done(job_id, output_path)
        _register_in_library(
            output_path, library_kind, tags=["image"],
            provenance={"prompt": prompt},
        )
    except HTTPException as e:
        jobs.mark_error(job_id, e.detail)
    except Exception as e:
        jobs.mark_error(job_id, str(e))
