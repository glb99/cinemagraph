"""FastAPI wrapper around CLIPSeg (via transformers) for text-prompted
semantic masking.

Same reason this exists as a service rather than a call-per-shell-out:
the model loads once at startup instead of paying the checkpoint-load /
device-init cost on every request. See machine-learning/README.md for the
/segment contract this implements and why CLIPSeg is loaded through
transformers rather than the timojl/clipseg repo directly (the latter
isn't a real PyPI package).
"""
import io

import numpy as np
import torch
from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image
from starlette.responses import Response
from transformers import CLIPSegForImageSegmentation, CLIPSegProcessor

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_NAME = "CIDAS/clipseg-rd64-refined"

app = FastAPI(title="machine-learning service")

_model = None
_processor = None


def _get_model():
    global _model, _processor
    if _model is None:
        print(f"Loading {MODEL_NAME} on {DEVICE} ...")
        _processor = CLIPSegProcessor.from_pretrained(MODEL_NAME)
        _model = CLIPSegForImageSegmentation.from_pretrained(MODEL_NAME).to(DEVICE)
        _model.eval()
        print("Model loaded.")
    return _model, _processor


@app.on_event("startup")
async def load_model_at_startup():
    # Eager-load so the first real request isn't the one paying the cost --
    # this is the whole point of being a service instead of a script.
    _get_model()


@app.get("/health")
async def health():
    return {"status": "ok", "device": DEVICE}


@app.post("/segment")
async def segment(image: UploadFile = File(...), prompt: str = Form(...)):
    model, processor = _get_model()

    image_bytes = await image.read()
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
    return Response(content=buf.getvalue(), media_type="image/png")
