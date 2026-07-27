"""FastAPI wrapper around Stability AI's Stable Audio Open model.

This is the entire reason this service exists as a wrapper rather than
cinemagraph-tool shelling out to a script per call: the model loads once,
at startup, instead of paying the multi-second checkpoint-load / GPU-init
cost on every single request. The actual generation logic is copied
verbatim from the proven-working experiment at
audio-effect-generation/generate_rain_stableaudio.py (see that project's
RESEARCH.md for why a serving layer wasn't worth it there, as a standalone
script -- it is here, since this service has a real caller now:
cinemagraph-tool's own server/app.py POST /generate/sound-effect).
"""
import io
import random

import torch
import torchaudio
from einops import rearrange
from fastapi import FastAPI
from pydantic import BaseModel
from stable_audio_tools import get_pretrained_model
from stable_audio_tools.inference.generation import generate_diffusion_cond
from starlette.responses import Response

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

app = FastAPI(title="sound-effects service")

_model = None
_model_config = None


def _get_model():
    global _model, _model_config
    if _model is None:
        print(f"Loading stabilityai/stable-audio-open-1.0 on {DEVICE} ...")
        model, model_config = get_pretrained_model("stabilityai/stable-audio-open-1.0")
        _model = model.to(DEVICE)
        _model_config = model_config
        print("Model loaded.")
    return _model, _model_config


@app.on_event("startup")
async def load_model_at_startup():
    # Eager-load so the first real request isn't the one paying the cost --
    # this is the whole point of being a service instead of a script.
    _get_model()


@app.get("/health")
async def health():
    return {"status": "ok", "device": DEVICE}


class GenerateRequest(BaseModel):
    prompt: str
    duration: float = 10.0  # seconds, max ~47s (the model's fixed latent window)
    steps: int = 100
    cfg_scale: float = 7.0
    seed: int = -1  # -1 = random


@app.post("/generate")
async def generate(req: GenerateRequest):
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

    buf = io.BytesIO()
    torchaudio.save(buf, output, sample_rate, format="wav")
    return Response(content=buf.getvalue(), media_type="audio/wav")
