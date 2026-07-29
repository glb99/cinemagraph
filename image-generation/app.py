"""FastAPI wrapper around Stable Diffusion XL for text-to-image generation.

This is the entire reason this service exists as a wrapper rather than
cinemagraph-tool shelling out to a script per call: the model loads once,
at startup, instead of paying the multi-second checkpoint-load / GPU-init
cost on every single request. Same shape as sound-effects/app.py -- eager
load in a startup hook, module-global singleton, one /generate endpoint --
deliberately kept consistent since both are the same kind of thing (a
single-purpose diffusion model wrapper), not because there's shared code
between them (there isn't, on purpose -- see docs/DESIGN.md sec 3.3 on why
isolated services never share code with each other).

SDXL, not a hosted API: a hosted option (Gemini's native image models) was
tried first and reverted -- new Google AI Studio accounts require a
non-refundable minimum prepay to use it at all, found only by actually
trying to generate an image. SDXL runs natively on an 8GB GPU (confirmed:
~7GB used, no quantization needed) at the cost of a real quality gap
against frontier hosted models -- an accepted tradeoff given the billing
friction. See docs/experiments/ for the full account of both attempts.

The VAE is swapped for madebyollin/sdxl-vae-fp16-fix rather than using
SDXL's own bundled VAE: found via a real OOM crash -- the diffusion loop
itself completed fine (30/30 steps), but decoding the final latents into
pixels failed with CUDA out of memory. Root cause: SDXL's own VAE is
NaN-unstable in fp16, so diffusers silently upcasts it to float32 to
compensate (visible in this exact deployment's own logs as an
`upcast_vae`/AutoencoderKL warning) -- doubling the VAE's memory footprint
at exactly the moment it's decoding a full 1024x1024 image, on top of
whatever the UNet+text encoders already used. The fp16-fix VAE is a
precision-corrected checkpoint that's numerically stable in fp16, so it
never needs that upcast at all. `enable_vae_slicing()` is kept on top as a
second, independent safety margin (decodes one image at a time from the
batch rather than all at once -- a no-op for this service's batch size of
1, but free and correct to leave on in case that ever changes).
"""
import io

import torch
from diffusers import AutoencoderKL, StableDiffusionXLPipeline
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.responses import Response

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
VAE_ID = "madebyollin/sdxl-vae-fp16-fix"

app = FastAPI(title="image-generation service")

_pipe = None


def _get_pipe():
    global _pipe
    if _pipe is None:
        dtype = torch.float16 if DEVICE == "cuda" else torch.float32
        print(f"Loading {MODEL_ID} on {DEVICE} ...")
        vae = AutoencoderKL.from_pretrained(VAE_ID, torch_dtype=dtype)
        _pipe = StableDiffusionXLPipeline.from_pretrained(
            MODEL_ID,
            vae=vae,
            torch_dtype=dtype,
            use_safetensors=True,
        )
        _pipe = _pipe.to(DEVICE)
        _pipe.enable_vae_slicing()
        print("Model loaded.")
    return _pipe


@app.on_event("startup")
async def load_model_at_startup():
    # Eager-load so the first real request isn't the one paying the cost --
    # this is the whole point of being a service instead of a script.
    _get_pipe()


@app.get("/health")
async def health():
    return {"status": "ok", "device": DEVICE}


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    steps: int = 30
    guidance_scale: float = 7.5
    width: int = 1024
    height: int = 1024
    seed: int = -1  # -1 = random


@app.post("/generate")
async def generate(req: GenerateRequest):
    pipe = _get_pipe()
    generator = torch.Generator(device=DEVICE).manual_seed(req.seed) if req.seed != -1 else None

    image = pipe(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt or None,
        num_inference_steps=req.steps,
        guidance_scale=req.guidance_scale,
        width=req.width,
        height=req.height,
        generator=generator,
    ).images[0]

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
