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
"""
import asyncio
import json
from pathlib import Path

from fastapi import HTTPException

from cinemagraph import pipeline

from . import jobs
from ._external_service import call_optional_service

MUSIC_POLL_INTERVAL_SECONDS = 2.0
MUSIC_POLL_MAX_ATTEMPTS = 150  # ~5 minutes total


def run_render_job(job_id: str, render_fn, output_path: Path, **kwargs) -> None:
    """Sync (threadpool) job: a purely local render, no external service."""
    jobs.mark_running(job_id)
    try:
        render_fn(output_path=str(output_path), **kwargs)
        jobs.mark_done(job_id, output_path)
    except Exception as e:
        jobs.mark_error(job_id, str(e))


async def run_photo_semantic_mask_job(
    job_id: str,
    output_path: Path,
    *,
    photo_path: Path,
    mask_path: Path,
    mask_prompt: str,
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
        resp = await call_optional_service(
            "ML_SERVICE_URL", "POST", "/segment", service_name="Semantic masking",
            data={"prompt": mask_prompt},
            files={"image": (photo_path.name, image_bytes, "application/octet-stream")},
            timeout=60.0,
        )
        mask_path.write_bytes(resp.content)

        await asyncio.to_thread(
            pipeline.save_cinemagraph_from_photo,
            photo_path=str(photo_path),
            output_path=str(output_path),
            mask_path=str(mask_path),
            **render_kwargs,
        )
        jobs.mark_done(job_id, output_path)
    except HTTPException as e:
        jobs.mark_error(job_id, e.detail)
    except Exception as e:
        jobs.mark_error(job_id, str(e))


async def run_music_job(
    job_id: str, output_path: Path, *, prompt: str, lyrics: str, duration: float, thinking: bool
) -> None:
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


async def run_sound_effect_job(job_id: str, output_path: Path, *, prompt: str, duration: float) -> None:
    """Unlike ACE-Step's queue, sound-effects/ answers in one synchronous
    call -- still a background job here because generation takes real time.
    """
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
