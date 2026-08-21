"""FastAPI wrapper around Stability AI's Stable Audio Open model.

This service exists as a wrapper rather than cinemagraph shelling out
to a script per call so the model stays resident *between* requests instead
of paying the multi-second checkpoint-load / GPU-init cost on every one.
The actual generation logic is copied verbatim from the proven-working
experiment at audio-effect-generation/generate_rain_stableaudio.py (see
that project's RESEARCH.md for why a serving layer wasn't worth it there,
as a standalone script -- it is here, since this service has a real caller
now: cinemagraph's own server/app.py POST /generate/sound-effect).

Residency is bounded by an idle TTL rather than lasting forever, for the
same measured reason as image-generation/app.py (see its docstring): this
project's GPU is 8.19GB and its Docker VM has ~10GB of RAM, so any two of
the torch satellites held resident at once OOM-kill something -- a real,
repeated `Exited (137)` on this exact service, not a hypothetical. The
model now loads on the first request and unloads after MODEL_TTL idle
seconds, freeing the GPU for whichever service is asked for next.
MODEL_TTL=0 keeps it resident forever (Immich's semantics); PRELOAD=1
loads at startup instead of on first use. The identical logic in the other
satellites is written out separately on purpose -- docs/DESIGN.md sec 3.3
forbids these services sharing code with each other.
"""
import asyncio
import gc
import io
import os
import random
import time
import wave

import numpy as np
import torch
from einops import rearrange
from fastapi import FastAPI
from pydantic import BaseModel
from stable_audio_tools import get_pretrained_model
from stable_audio_tools.inference.generation import generate_diffusion_cond
from starlette.responses import JSONResponse, Response

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_TTL = int(os.environ.get("MODEL_TTL", "900"))
PRELOAD = os.environ.get("PRELOAD", "").lower() in ("1", "true", "yes")
# Seconds a single generation may hold the model before this process is
# assumed wedged and kills itself. 0 disables the busy watchdog entirely.
#
# The idle TTL above cannot cover this case, and used to be actively defeated
# by it: a hung generation holds _generation_lock forever, so the reaper --
# which took that same lock before unloading -- blocked behind it
# permanently. See docs/experiments/2026-08-21-model-lifecycle-prior-art.md.
BUSY_TIMEOUT = int(os.environ.get("BUSY_TIMEOUT", "1200"))
# How long the supervisor waits for the lock before giving up for this tick.
# It must never block indefinitely -- that was the original bug.
LOCK_ACQUIRE_TIMEOUT = 5.0

app = FastAPI(title="sound-effects service")

_model = None
_model_config = None
_last_used = 0.0
# When the in-flight generation started, or None when nothing is running.
# This is what distinguishes "busy" from "wedged"; the lock alone cannot,
# since it looks identical in both cases.
_inflight_since: float | None = None
# Serializes GPU work. It no longer doubles as the reaper's guard -- the
# supervisor now acquires it with a timeout instead of waiting forever.
_generation_lock = asyncio.Lock()


def _get_model():
    global _model, _model_config
    if _model is None:
        print(f"Loading stabilityai/stable-audio-open-1.0 on {DEVICE} ...")
        model, model_config = get_pretrained_model("stabilityai/stable-audio-open-1.0")
        _model = model.to(DEVICE)
        _model_config = model_config
        print("Model loaded.")
    return _model, _model_config


def _unload():
    """Drops the model and returns its VRAM to the driver. gc.collect()
    before empty_cache(): the latter only releases blocks the allocator
    already considers free, so the objects owning those tensors have to be
    collected first or the call does nothing."""
    global _model, _model_config
    if _model is None:
        return
    print("Unloading model (idle).")
    _model = None
    _model_config = None
    gc.collect()
    if DEVICE == "cuda":
        torch.cuda.empty_cache()
    print("Model unloaded.")


def _poll_interval() -> int:
    """Cap the supervisor's tick at 30s, but never poll slower than whichever
    deadline it's enforcing."""
    deadlines = [t for t in (MODEL_TTL, BUSY_TIMEOUT) if t > 0]
    return max(1, min(30, min(deadlines))) if deadlines else 30


def _exit_if_wedged() -> None:
    """Kills this process when a generation has held the model past
    BUSY_TIMEOUT.

    Exiting looks drastic, and it is the only thing that reliably works. A
    hung CUDA call doesn't raise into Python, so there is nothing to catch;
    the thread running it can't be cancelled; and torch.cuda.empty_cache()
    can't return memory the wedged interpreter still owns. Killing the
    process is what hands the card back to the driver -- so the container
    must have a restart policy, or this trades a stuck service for a missing
    one (see docker-compose.yml).

    os._exit, not sys.exit: sys.exit unwinds and would block on the very
    to_thread worker that's stuck. That also skips flushing stdout, hence
    flush=True on the way out -- losing the reason would make this look like
    an unexplained crash.
    """
    if BUSY_TIMEOUT <= 0 or _inflight_since is None:
        return
    busy_for = time.monotonic() - _inflight_since
    if busy_for < BUSY_TIMEOUT:
        return
    print(
        f"FATAL: generation in flight for {busy_for:.0f}s, past BUSY_TIMEOUT="
        f"{BUSY_TIMEOUT}s. Assuming wedged; exiting to release the GPU.",
        flush=True,
    )
    os._exit(1)


async def _supervise_model():
    """Unloads the model once it's gone MODEL_TTL seconds without use, and
    kills the process if a generation wedges past BUSY_TIMEOUT.

    Polls a timestamp rather than arming a per-request timer -- a timer
    would need cancelling/rescheduling around every generation including
    failed ones, and getting that wrong silently reverts this service to
    holding the GPU forever, the exact failure this exists to prevent.

    The unload path takes _generation_lock with a timeout and simply skips
    this tick if it can't get it. Waiting unconditionally is what let one
    stuck generation disable idle unloading permanently.
    """
    interval = _poll_interval()
    while True:
        await asyncio.sleep(interval)
        _exit_if_wedged()
        if MODEL_TTL <= 0 or _model is None:
            continue
        # A generation that's running but not yet wedged: leave it alone, and
        # don't queue up behind it either.
        if _inflight_since is not None or time.monotonic() - _last_used < MODEL_TTL:
            continue
        try:
            await asyncio.wait_for(
                _generation_lock.acquire(), timeout=LOCK_ACQUIRE_TIMEOUT
            )
        except asyncio.TimeoutError:
            continue
        try:
            if _model is not None and time.monotonic() - _last_used >= MODEL_TTL:
                _unload()
        finally:
            _generation_lock.release()


@app.on_event("startup")
async def on_startup():
    if PRELOAD:
        _get_model()
    if MODEL_TTL > 0 or BUSY_TIMEOUT > 0:
        asyncio.create_task(_supervise_model())


@app.get("/health")
async def health():
    """Reports in-flight state, not just "the event loop is alive".

    A wedged satellite used to answer a plain 200 here forever: /generate
    holds the lock, but this route never took it, so the process looked
    perfectly healthy while being unable to complete another request.
    core's service_available() believed it, and /capabilities kept
    advertising sound-effect generation that could no longer work.

    Only *wedged* fails the check -- an ordinary in-progress generation is
    not unhealthy, and 503-ing on one would make /capabilities flap every
    time someone generates something. Note the window this 503 is visible
    for is short by design: the supervisor kills the process on its next
    tick. The durable signal is the connection refusal that follows.
    """
    # Never loads the model -- core's /capabilities polls this, and a health
    # check that seizes the GPU would defeat the whole point of the TTL.
    inflight_since = _inflight_since
    inflight_seconds = (
        round(time.monotonic() - inflight_since, 1) if inflight_since is not None else None
    )
    wedged = (
        BUSY_TIMEOUT > 0
        and inflight_seconds is not None
        and inflight_seconds >= BUSY_TIMEOUT
    )
    return JSONResponse(
        {
            "status": "stuck" if wedged else "ok",
            "device": DEVICE,
            "model_loaded": _model is not None,
            "inflight_seconds": inflight_seconds,
        },
        status_code=503 if wedged else 200,
    )


class GenerateRequest(BaseModel):
    prompt: str
    duration: float = 10.0  # seconds, max ~47s (the model's fixed latent window)
    steps: int = 100
    cfg_scale: float = 7.0
    seed: int = -1  # -1 = random


def _run_generate(req: "GenerateRequest") -> bytes:
    model, model_config = _get_model()
    sample_rate = model_config["sample_rate"]
    sample_size = model_config["sample_size"]

    conditioning = [{
        "prompt": req.prompt,
        "seconds_start": 0,
        "seconds_total": req.duration,
    }]
    seed = req.seed if req.seed != -1 else random.randint(0, 2**32 - 2)

    output = generate_diffusion_cond(
        model,
        steps=req.steps,
        cfg_scale=req.cfg_scale,
        conditioning=conditioning,
        sample_size=sample_size,
        sample_rate=sample_rate,
        seed=seed,
        device=DEVICE,
    )

    output = rearrange(output, "b d n -> d (b n)")
    output = output.to(torch.float32).div(torch.max(torch.abs(output))).clamp(-1, 1).cpu()

    # generate_diffusion_cond always fills the model's full fixed-size latent
    # window (~47.6s) regardless of the requested duration, so trim the tail.
    num_samples = int(req.duration * sample_rate)
    output = output[:, :num_samples]

    # Written directly via the stdlib `wave` module rather than
    # torchaudio.save: recent torchaudio releases delegate WAV encoding to
    # an optional `torchcodec` backend, which itself needs real system-level
    # FFmpeg shared libraries this image doesn't have -- found via a real
    # request against a real GPU, not from reading changelogs. WAV is a
    # simple enough format not to need a library for it at all.
    pcm = (output.numpy() * 32767.0).astype(np.int16)  # (channels, samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(pcm.shape[0])
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.T.tobytes())  # interleave channels for multi-channel output
    return buf.getvalue()


@app.post("/generate")
async def generate(req: GenerateRequest):
    """The generation itself is pushed off the event loop with
    asyncio.to_thread. It used to run synchronously inside this async def --
    the same event-loop-blocking bug already found and fixed in
    image-generation/app.py and documented in docs/DESIGN.md sec 3.7, which
    made /health unanswerable for the whole duration of a generation and so
    made core's /capabilities intermittently report this service as down.
    It was never noticed here because nothing polled this service *during* a
    generation until the idle reaper below started needing to run alongside
    one.
    """
    global _last_used, _inflight_since
    async with _generation_lock:
        # Stamped before the work as well as after: the reaper reads this
        # timestamp, and a long generation must not look idle while running.
        # _inflight_since is the busy watchdog's own input; clearing it in a
        # finally matters as much as setting it, since a generation that
        # raised would otherwise look permanently in-flight and get the
        # process killed for a failure it already reported cleanly.
        _last_used = _inflight_since = time.monotonic()
        try:
            wav_bytes = await asyncio.to_thread(_run_generate, req)
            if DEVICE == "cuda":
                # Mirrors image-generation's own post-request cache clear:
                # this GPU is tight enough that the caching allocator's
                # freed-but-held blocks fragment across calls.
                torch.cuda.empty_cache()
        finally:
            _inflight_since = None
            _last_used = time.monotonic()
    return Response(content=wav_bytes, media_type="audio/wav")
