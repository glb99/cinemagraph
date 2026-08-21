"""FastAPI wrapper around CLIPSeg (via transformers) for text-prompted
semantic masking.

Same reason this exists as a service rather than a call-per-shell-out: the
model stays resident *between* requests instead of paying the
checkpoint-load / device-init cost on every one. See
machine-learning/README.md for the /segment contract this implements and
why CLIPSeg is loaded through transformers rather than the timojl/clipseg
repo directly (the latter isn't a real PyPI package).

Residency is bounded by an idle TTL rather than lasting forever, matching
the other satellites (see image-generation/app.py's docstring for the
measured VRAM/RAM numbers that forced it). CLIPSeg is by far the smallest
of them -- a few hundred MB, and it currently runs on CPU here since this
service's Dockerfile installs the CPU torch wheel -- so the TTL matters
less for this one specifically; it's applied anyway so all three satellites
behave the same way rather than one being a special case someone has to
remember. MODEL_TTL=0 keeps it resident forever (Immich's semantics, whose
naming this whole service already follows); PRELOAD=1 loads at startup
instead of on first use. The identical logic in the other satellites is
written out separately on purpose -- docs/DESIGN.md sec 3.3 forbids these
services sharing code with each other.

An idle TTL only handles a model nobody is using. A *stuck* one is the
opposite case, and it used to defeat the TTL outright: a hung request holds
the inference lock forever, and the reaper waited on that same lock before
unloading, so it blocked permanently -- the model stayed resident and the
GPU stayed occupied, precisely when releasing it mattered most. BUSY_TIMEOUT
adds the missing half (LocalAI's idle/busy watchdog pair): past that many
seconds in a single request, this process kills itself so the container
restarts and the driver gets the card back. Research and the reasoning for
picking this over a VRAM-aware scheduler:
docs/experiments/2026-08-21-model-lifecycle-prior-art.md.
"""
import asyncio
import gc
import io
import os
import time

import numpy as np
import torch
from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image
from starlette.responses import JSONResponse, Response
from transformers import CLIPSegForImageSegmentation, CLIPSegProcessor

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_NAME = "CIDAS/clipseg-rd64-refined"
MODEL_TTL = int(os.environ.get("MODEL_TTL", "900"))
PRELOAD = os.environ.get("PRELOAD", "").lower() in ("1", "true", "yes")
# Seconds a single request may hold the model before this process is assumed
# wedged and kills itself. 0 disables the busy watchdog entirely.
#
# The idle TTL above cannot cover this case, and used to be actively defeated
# by it: a hung request holds _inference_lock forever, so the reaper -- which
# took that same lock before unloading -- blocked behind it permanently. See
# docs/experiments/2026-08-21-model-lifecycle-prior-art.md.
BUSY_TIMEOUT = int(os.environ.get("BUSY_TIMEOUT", "1200"))
# How long the supervisor waits for the lock before giving up for this tick.
# It must never block indefinitely -- that was the original bug.
LOCK_ACQUIRE_TIMEOUT = 5.0

app = FastAPI(title="machine-learning service")

_model = None
_processor = None
_last_used = 0.0
# When the in-flight request started, or None when nothing is running. This
# is what distinguishes "busy" from "wedged"; the lock alone cannot, since it
# looks identical in both cases.
_inflight_since: float | None = None
# Serializes inference. It no longer doubles as the reaper's guard -- the
# supervisor now acquires it with a timeout instead of waiting forever.
_inference_lock = asyncio.Lock()


def _get_model():
    global _model, _processor
    if _model is None:
        print(f"Loading {MODEL_NAME} on {DEVICE} ...")
        _processor = CLIPSegProcessor.from_pretrained(MODEL_NAME)
        _model = CLIPSegForImageSegmentation.from_pretrained(MODEL_NAME).to(DEVICE)
        _model.eval()
        print("Model loaded.")
    return _model, _processor


def _unload():
    """Drops the model and returns its memory. gc.collect() before
    empty_cache(): the latter only releases blocks the allocator already
    considers free, so the objects owning those tensors have to be collected
    first or the call does nothing."""
    global _model, _processor
    if _model is None:
        return
    print("Unloading model (idle).")
    _model = None
    _processor = None
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
    """Kills this process when a request has held the model past
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
        f"FATAL: request in flight for {busy_for:.0f}s, past BUSY_TIMEOUT="
        f"{BUSY_TIMEOUT}s. Assuming wedged; exiting to release the GPU.",
        flush=True,
    )
    os._exit(1)


async def _supervise_model():
    """Unloads the model once it's gone MODEL_TTL seconds without use, and
    kills the process if a request wedges past BUSY_TIMEOUT.

    Polls a timestamp rather than arming a per-request timer -- a timer
    would need cancelling/rescheduling around every request including failed
    ones, and getting that wrong silently reverts this service to holding
    its memory forever, the exact failure this exists to prevent.

    The unload path takes _inference_lock with a timeout and simply skips
    this tick if it can't get it. Waiting unconditionally is what let one
    stuck request disable idle unloading permanently.
    """
    interval = _poll_interval()
    while True:
        await asyncio.sleep(interval)
        _exit_if_wedged()
        if MODEL_TTL <= 0 or _model is None:
            continue
        # A request that's running but not yet wedged: leave it alone, and
        # don't queue up behind it either.
        if _inflight_since is not None or time.monotonic() - _last_used < MODEL_TTL:
            continue
        try:
            await asyncio.wait_for(
                _inference_lock.acquire(), timeout=LOCK_ACQUIRE_TIMEOUT
            )
        except asyncio.TimeoutError:
            continue
        try:
            if _model is not None and time.monotonic() - _last_used >= MODEL_TTL:
                _unload()
        finally:
            _inference_lock.release()


@app.on_event("startup")
async def on_startup():
    if PRELOAD:
        _get_model()
    if MODEL_TTL > 0 or BUSY_TIMEOUT > 0:
        asyncio.create_task(_supervise_model())


@app.get("/health")
async def health():
    """Reports in-flight state, not just "the event loop is alive".

    A wedged satellite used to answer a plain 200 here forever: /segment
    holds the lock, but this route never took it, so the process looked
    perfectly healthy while being unable to complete another request.
    core's service_available() believed it, and /capabilities kept
    advertising semantic masking that could no longer work.

    Only *wedged* fails the check -- an ordinary in-progress request is not
    unhealthy, and 503-ing on one would make /capabilities flap every time
    someone generates something. Note the window this 503 is visible for is
    short by design: the supervisor kills the process on its next tick. The
    durable signal is the connection refusal that follows.
    """
    # Never loads the model -- core's /capabilities polls this, and a health
    # check that loads on demand would defeat the point of the TTL.
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


def _run_segment(image_bytes: bytes, prompt: str) -> bytes:
    model, processor = _get_model()

    pil_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    inputs = processor(text=[prompt], images=[pil_image], return_tensors="pt").to(DEVICE)
    with torch.no_grad():
        logits = model(**inputs).logits

    # CLIPSeg's raw logits are a similarity heatmap, not yet a 0..1 mask --
    # sigmoid turns them into one, matching cinemagraph.mask.load_mask()'s
    # white=match/black=no-match convention.
    probs = torch.sigmoid(logits).cpu().numpy()
    if probs.ndim == 3:
        probs = probs[0]

    mask_image = Image.fromarray((probs * 255).astype(np.uint8), mode="L")
    mask_image = mask_image.resize(pil_image.size, resample=Image.BILINEAR)

    buf = io.BytesIO()
    mask_image.save(buf, format="PNG")
    return buf.getvalue()


@app.post("/segment")
async def segment(image: UploadFile = File(...), prompt: str = Form(...)):
    """Inference is pushed off the event loop with asyncio.to_thread -- same
    reasoning as image-generation/app.py's /generate (docs/DESIGN.md sec
    3.7): a synchronous torch call inside an async def blocks this whole
    process, including /health, for its full duration. Cheap here (CLIPSeg
    is small) but required for the idle reaper to be able to run at all
    while a request is in flight."""
    global _last_used, _inflight_since
    image_bytes = await image.read()
    async with _inference_lock:
        # _inflight_since is what the busy watchdog reads; clearing it in a
        # finally matters as much as setting it, since a request that raised
        # would otherwise look permanently in-flight and get the process
        # killed for a failure it already reported cleanly.
        _last_used = _inflight_since = time.monotonic()
        try:
            png_bytes = await asyncio.to_thread(_run_segment, image_bytes, prompt)
        finally:
            _inflight_since = None
            _last_used = time.monotonic()
    return Response(content=png_bytes, media_type="image/png")
