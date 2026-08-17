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
from starlette.responses import Response
from transformers import CLIPSegForImageSegmentation, CLIPSegProcessor

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_NAME = "CIDAS/clipseg-rd64-refined"
MODEL_TTL = int(os.environ.get("MODEL_TTL", "900"))
PRELOAD = os.environ.get("PRELOAD", "").lower() in ("1", "true", "yes")

app = FastAPI(title="machine-learning service")

_model = None
_processor = None
_last_used = 0.0
# Serializes inference and doubles as the reaper's guard, so the model can
# never be unloaded out from under a request still in flight.
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


async def _reap_idle_model():
    """Unloads the model once it's gone MODEL_TTL seconds without use.
    Polls a timestamp rather than arming a per-request timer -- a timer
    would need cancelling/rescheduling around every request including failed
    ones, and getting that wrong silently reverts this service to holding
    its memory forever, the exact failure this exists to prevent."""
    interval = max(1, min(30, MODEL_TTL))
    while True:
        await asyncio.sleep(interval)
        if _model is None or time.monotonic() - _last_used < MODEL_TTL:
            continue
        async with _inference_lock:
            if _model is not None and time.monotonic() - _last_used >= MODEL_TTL:
                _unload()


@app.on_event("startup")
async def on_startup():
    if PRELOAD:
        _get_model()
    if MODEL_TTL > 0:
        asyncio.create_task(_reap_idle_model())


@app.get("/health")
async def health():
    # Never loads the model -- core's /capabilities polls this, and a health
    # check that loads on demand would defeat the point of the TTL.
    return {"status": "ok", "device": DEVICE, "model_loaded": _model is not None}


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
    global _last_used
    image_bytes = await image.read()
    async with _inference_lock:
        _last_used = time.monotonic()
        png_bytes = await asyncio.to_thread(_run_segment, image_bytes, prompt)
        _last_used = time.monotonic()
    return Response(content=png_bytes, media_type="image/png")
