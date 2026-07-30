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

Supports an optional reference image (img2img) alongside plain
text-to-image: `StableDiffusionXLImg2ImgPipeline.from_pipe(pipe)` wraps the
*same already-loaded* UNet/VAE/text-encoders in a different pipeline class
with different __call__ semantics (image=, strength= instead of width=/
height=) -- confirmed directly against the installed diffusers==0.39.0
(`from_pipe` exists, `__call__` accepts `image`/`strength`), not assumed.
`/generate` therefore switched from a JSON body to multipart/form-data -- a
request can now carry an optional uploaded image file, which JSON can't
represent cleanly.

The img2img wrapper is built *lazily*, on the first actual img2img request,
not eagerly at startup alongside the txt2img pipe -- found via a real CUDA
OOM, not assumed safe from "shares the same weights" alone: eager-loading
both at once left this GPU sitting at ~7.9/8.19GB *at rest*, before a single
request came in, which was enough to OOM even a plain text-to-image
request's own activation memory during the UNet's attention forward pass
(not the VAE decode step this file's other OOM note is about -- a different
failure point). `from_pipe()` does share the big weight tensors, but
apparently not for free on a card this tight -- something about holding two
distinct pipeline objects costs real, measurable VRAM here. Building it
lazily keeps the common txt2img path exactly as it always was; only an
actual img2img request pays that cost, whenever it first happens.

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
from diffusers import AutoencoderKL, StableDiffusionXLImg2ImgPipeline, StableDiffusionXLPipeline
from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image
from starlette.responses import Response

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
VAE_ID = "madebyollin/sdxl-vae-fp16-fix"

app = FastAPI(title="image-generation service")

_pipe = None
_img2img_pipe = None


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
        # Added alongside the img2img feature: found via a real OOM inside
        # the UNet's own cross-attention forward pass (not the VAE) once
        # img2img was in the mix -- this GPU has only ~1GB of headroom above
        # the shared pipeline's own baseline, and img2img's extra work (VAE
        # *encoding* the reference image, something txt2img never does) ate
        # into it. Attention slicing computes cross-attention in chunks
        # instead of all at once -- lower peak memory, some speed cost --
        # and, like VAE slicing above, the flag lives on the shared UNet
        # module itself, so it applies to the img2img pipe automatically too
        # once from_pipe() wraps the same components.
        _pipe.enable_attention_slicing()
        print("Model loaded.")
    return _pipe


def _get_img2img_pipe():
    # from_pipe() wraps the *same* already-loaded UNet/VAE/text-encoders in a
    # different pipeline class -- no second copy of the model in VRAM, just a
    # different __call__ contract (image=/strength= instead of width=/height=).
    global _img2img_pipe
    if _img2img_pipe is None:
        _img2img_pipe = StableDiffusionXLImg2ImgPipeline.from_pipe(_get_pipe())
    return _img2img_pipe


@app.on_event("startup")
async def load_model_at_startup():
    # Eager-load the txt2img pipe so the first real request isn't the one
    # paying the cost -- this is the whole point of being a service instead
    # of a script. The img2img wrapper deliberately is NOT built here too --
    # see this module's own docstring for the real OOM that caused this.
    _get_pipe()


@app.get("/health")
async def health():
    return {"status": "ok", "device": DEVICE}


@app.post("/generate")
async def generate(
    prompt: str = Form(...),
    negative_prompt: str = Form(""),
    steps: int = Form(30),
    guidance_scale: float = Form(7.5),
    width: int = Form(1024),
    height: int = Form(1024),
    seed: int = Form(-1),  # -1 = random
    image: UploadFile | None = File(None),
    strength: float = Form(0.6),  # img2img only: 0=stay close to reference, 1=ignore it
):
    generator = torch.Generator(device=DEVICE).manual_seed(seed) if seed != -1 else None

    if image is not None:
        # img2img: output dimensions follow the reference image, not width/height
        # (which don't apply to this pipeline -- the diffusion process starts
        # from a noised version of the reference instead of pure noise).
        reference = Image.open(io.BytesIO(await image.read())).convert("RGB")
        result = _get_img2img_pipe()(
            prompt=prompt,
            negative_prompt=negative_prompt or None,
            image=reference,
            strength=strength,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            generator=generator,
        ).images[0]
    else:
        result = _get_pipe()(
            prompt=prompt,
            negative_prompt=negative_prompt or None,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            width=width,
            height=height,
            generator=generator,
        ).images[0]

    if DEVICE == "cuda":
        # Found necessary via a real OOM: a second img2img call failed even
        # though the first one succeeded, on a GPU this tight -- PyTorch's
        # CUDA caching allocator holds freed-but-not-released memory between
        # calls, and can fragment enough after a shape/pipeline switch (first
        # txt2img, then a differently-shaped img2img call) that a later
        # allocation fails even with "enough" total free memory reported.
        # Emptying the cache after every request keeps that from compounding
        # across calls -- a small, fixed cost every time, not a proportional
        # one to the problem it prevents.
        torch.cuda.empty_cache()

    buf = io.BytesIO()
    result.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
