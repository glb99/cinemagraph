"""FastAPI wrapper around Stable Diffusion XL for text-to-image generation.

This service exists as a wrapper rather than cinemagraph-tool shelling out
to a script per call so that the model stays resident *between* requests
instead of paying the multi-second checkpoint-load / GPU-init cost on every
one. Same shape as sound-effects/app.py -- module-global singleton behind a
lazy getter, one /generate endpoint -- deliberately kept consistent since
both are the same kind of thing (a single-purpose diffusion model wrapper),
not because there's shared code between them (there isn't, on purpose --
see docs/DESIGN.md sec 3.3 on why isolated services never share code with
each other; that rule is why the identical TTL logic below is written out
separately in each service instead of being factored into a shared module).

Residency is bounded by an idle TTL rather than lasting forever. Measured
on this project's actual hardware: SDXL alone sits at ~7.1GB of an 8.19GB
card, so while this service holds its model *nothing else can load* --
sound-effects (Stable Audio Open) and ACE-Step each need several GB of
their own, and the host's Docker VM has only ~10GB of RAM for all of them
put together. Holding weights forever therefore didn't just waste memory,
it made the satellites mutually exclusive: starting a second one
OOM-killed something (a real, repeated `Exited (137)`, not a hypothetical).
So the model now loads on the first request that needs it and unloads
after MODEL_TTL idle seconds, freeing the GPU for whichever service is
asked for next. This is the pattern Immich uses for its own ML service
(MACHINE_LEARNING_MODEL_TTL, default 300s, 0 = never unload) and Ollama
for model residency (OLLAMA_KEEP_ALIVE) -- the env var's semantics here
follow Immich's, matching the convention this project already borrows from
them elsewhere (see machine-learning/'s and server/'s own naming).

The tradeoff is real, and on this deployment it is much larger than it
looks -- measured, not estimated: a cold load here takes **~6 minutes**
(`Loading pipeline components...: 7/7 [06:08, 52.64s/it]`), against ~30s
for the generation itself. That is not SDXL being slow; it is this
service's Hugging Face cache being a *bind mount*
(./data/image-generation-cache) read through Docker Desktop's WSL2
cross-OS file-sharing layer -- the identical `p9_client_rpc` pathology
already diagnosed for ACE-Step and documented in docker-compose.yml, where
it was fixed by switching that service to **named volumes**. The same fix
has never been applied here or to sound-effects/, which is why the reload
cost is what it is. Until it is, keep MODEL_TTL generous (see compose):
unloading a model that takes 6 minutes to get back is worse than holding
it, unless another service genuinely needs the card.

MODEL_TTL=0 opts back into the old always-resident behaviour when this
service is the only GPU tenant; PRELOAD=1 loads at startup rather than on
first use (paying that 6 minutes once, up front, as this service did
unconditionally before).

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
import asyncio
import gc
import io
import os
import time

import torch
from diffusers import AutoencoderKL, StableDiffusionXLImg2ImgPipeline, StableDiffusionXLPipeline
from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image
from starlette.responses import Response

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
VAE_ID = "madebyollin/sdxl-vae-fp16-fix"

# Seconds the pipeline may sit unused before it's unloaded and its VRAM
# released. 0 disables unloading entirely (Immich's MACHINE_LEARNING_MODEL_TTL
# semantics, not Ollama's -- there 0 means "unload immediately"; the two
# conventions genuinely disagree, and this project follows Immich elsewhere).
MODEL_TTL = int(os.environ.get("MODEL_TTL", "900"))
# Load at startup instead of on first request. Off by default: eager loading
# is exactly what made this service hold the whole GPU while idle.
PRELOAD = os.environ.get("PRELOAD", "").lower() in ("1", "true", "yes")

app = FastAPI(title="image-generation service")

_pipe = None
_img2img_pipe = None
_last_used = 0.0
# Serializes actual GPU generation calls -- this card can't run two at once
# anyway (see the VRAM notes above), and asyncio.to_thread alone would let
# concurrent requests launch truly parallel threads both hitting the GPU,
# which is worse than the accidental serialization the blocking-call bug
# below used to provide as an (unintended) side effect. The idle reaper takes
# this same lock, which is what keeps it from unloading the pipeline out from
# under a generation that's still running.
_generation_lock = asyncio.Lock()


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


def _unload():
    """Drops the pipelines and returns their VRAM to the driver.

    Both globals have to go: _img2img_pipe holds references to the very
    same UNet/VAE/text-encoder modules _pipe does (that's the whole point of
    from_pipe()), so clearing only _pipe would free nothing at all whenever
    an img2img request had ever been served. gc.collect() before
    empty_cache() for the same reason -- empty_cache only releases blocks
    the allocator already considers free, so the Python objects owning those
    tensors must actually be collected first or the call is a no-op.
    """
    global _pipe, _img2img_pipe
    if _pipe is None:
        return
    print("Unloading model (idle).")
    _pipe = None
    _img2img_pipe = None
    gc.collect()
    if DEVICE == "cuda":
        torch.cuda.empty_cache()
    print("Model unloaded.")


async def _reap_idle_model():
    """Unloads the pipeline once it's gone MODEL_TTL seconds without use.

    Polls rather than arming a timer per request: a timer would have to be
    cancelled and rescheduled around every generation (including ones that
    fail), and getting that wrong silently reverts this service to holding
    the GPU forever -- the exact bug this is here to prevent. Polling has no
    such failure mode; it just reads a timestamp.
    """
    interval = max(1, min(30, MODEL_TTL))
    while True:
        await asyncio.sleep(interval)
        if _pipe is None or time.monotonic() - _last_used < MODEL_TTL:
            continue
        # Same lock a generation holds, so this can never pull the model out
        # from under one in flight; it simply waits for it to finish.
        async with _generation_lock:
            if _pipe is not None and time.monotonic() - _last_used >= MODEL_TTL:
                _unload()


@app.on_event("startup")
async def on_startup():
    if PRELOAD:
        # The img2img wrapper deliberately is NOT built here too -- see this
        # module's own docstring for the real OOM that caused this.
        _get_pipe()
    if MODEL_TTL > 0:
        asyncio.create_task(_reap_idle_model())


@app.get("/health")
async def health():
    # Deliberately does not touch _get_pipe(): a health check must never be
    # what loads a multi-GB model onto the GPU. core's /capabilities polls
    # this route, so making it a loader would mean merely *looking at* the UI
    # seizes the card from whichever service actually needs it.
    return {"status": "ok", "device": DEVICE, "model_loaded": _pipe is not None}


def _run_txt2img(prompt, negative_prompt, steps, guidance_scale, width, height, generator):
    return _get_pipe()(
        prompt=prompt,
        negative_prompt=negative_prompt or None,
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
        width=width,
        height=height,
        generator=generator,
    ).images[0]


def _run_img2img(prompt, negative_prompt, image, strength, steps, guidance_scale, generator):
    return _get_img2img_pipe()(
        prompt=prompt,
        negative_prompt=negative_prompt or None,
        image=image,
        strength=strength,
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
        generator=generator,
    ).images[0]


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
    """Found via a real bug, not assumed correct: this used to call the SDXL
    pipeline directly, synchronously, inside this async def -- a purely
    CPU/GPU-bound call with no await, blocking this entire process's event
    loop for the whole duration of a generation (seconds to tens of seconds).
    That meant /health couldn't be answered either, during that whole
    window -- confirmed directly (curl /health while a generate call was in
    flight returned nothing for 3+ seconds) -- which explains why core's own
    /capabilities check would intermittently time out and report
    image_generation: false for a demonstrably-otherwise-healthy service.
    The exact same class of bug already found and documented in ACE-Step's
    own ensure_models_initialized (docs/DESIGN.md sec 3.7) -- this file had
    it too, just never noticed until /capabilities' own flakiness led back
    to it. asyncio.to_thread offloads the actual pipeline call to a worker
    thread, freeing this process's event loop to keep answering /health (and
    any other request) while generation runs. _generation_lock still
    serializes the *actual* GPU work -- to_thread alone would let concurrent
    requests launch truly parallel threads both hitting this GPU's already
    tight VRAM budget at once, which is strictly worse than one at a time.
    """
    global _last_used
    generator = torch.Generator(device=DEVICE).manual_seed(seed) if seed != -1 else None

    async with _generation_lock:
        # Stamped on both sides of the work: before, so the reaper can't
        # decide this model is idle while a long generation is still running
        # (it holds the lock, but the timestamp is what it reads); after, so
        # the TTL counts from when the GPU actually went quiet rather than
        # from when the request happened to arrive.
        _last_used = time.monotonic()
        if image is not None:
            # img2img: output dimensions follow the reference image, not
            # width/height (which don't apply to this pipeline -- the
            # diffusion process starts from a noised version of the
            # reference instead of pure noise).
            reference = Image.open(io.BytesIO(await image.read())).convert("RGB")
            result = await asyncio.to_thread(
                _run_img2img, prompt, negative_prompt, reference, strength,
                steps, guidance_scale, generator,
            )
        else:
            result = await asyncio.to_thread(
                _run_txt2img, prompt, negative_prompt, steps, guidance_scale,
                width, height, generator,
            )

        if DEVICE == "cuda":
            # Found necessary via a real OOM: a second img2img call failed
            # even though the first one succeeded, on a GPU this tight --
            # PyTorch's CUDA caching allocator holds freed-but-not-released
            # memory between calls, and can fragment enough after a
            # shape/pipeline switch (first txt2img, then a differently-shaped
            # img2img call) that a later allocation fails even with "enough"
            # total free memory reported. Emptying the cache after every
            # request keeps that from compounding across calls -- a small,
            # fixed cost every time, not a proportional one to the problem it
            # prevents. Kept inside the lock: releasing the lock before this
            # runs would let the next request's own generation start
            # allocating while this cache-clear is still in flight.
            torch.cuda.empty_cache()
        _last_used = time.monotonic()

    buf = io.BytesIO()
    result.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
