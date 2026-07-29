# image-generation

Isolated FastAPI service wrapping [Stable Diffusion XL](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)
(via [`diffusers`](https://github.com/huggingface/diffusers)) for text-prompted image
generation — the model backing `cinemagraph-tool`'s `POST /generate/image`.

## Why a separate service

Same reasoning as `machine-learning/`/`sound-effects/` (see `docs/DESIGN.md` §3.2/§5.7/§5.2):
`torch`/`diffusers` are multi-GB dependencies with no business in the core `cinemagraph-tool`
image, and generating an input image isn't part of what `cinemagraph` (the animate-a-photo
library) does — it's a separate capability that happens to feed the same pipeline.

## Why SDXL, not a hosted API

A hosted API (Gemini's native image models) was tried first and reverted: new Google AI Studio
accounts require a non-refundable minimum prepay (currently $10) to use the API at all, discovered
only by actually trying to generate an image against a real key, not from reading pricing docs.
SDXL was chosen after comparing current (2026) quality/VRAM data across local models: it's the best
fit for an 8GB GPU (~7GB used natively, no quantization needed), though it's rated behind Flux 2,
Imagen 4, and Midjourney v7 on photorealism — an accepted tradeoff given the billing friction on the
hosted route. See `docs/experiments/` for the full account of both attempts.

## Running it

```bash
cd image-generation
uv run uvicorn app:app --port 8005
```

`uv run` auto-creates this directory's own `.venv` and installs from its own `pyproject.toml`
the first time it's called — same `uv`-first workflow as the root project, just a separate
project/lockfile per the isolation-boundary rule (see `docs/DESIGN.md` §3.2/§7). Plain
`pip install . && uvicorn app:app --port 8005` works too if you'd rather manage the venv
yourself.

Or via Docker Compose from the repo root: `docker compose --profile image up image-generation`.

First request (or startup, since the model loads eagerly) downloads several GB of weights from the
Hugging Face Hub — cached afterward (`HF_HOME`/`/cache` volume in the Dockerfile/compose entry).
CPU-capable but very slow; a GPU (CUDA) is picked up automatically if available
(`torch.cuda.is_available()`).

## API

```
GET /health
  200 {"status": "ok", "device": "cuda" | "cpu"}

POST /generate
  {"prompt": "...", "negative_prompt": "", "steps": 30, "guidance_scale": 7.5,
   "width": 1024, "height": 1024, "seed": -1}
  -> image/png bytes

  steps: diffusion sampling steps (fewer = faster, lower quality)
  seed: -1 for random
```

## A note on the VAE

`app.py` loads `madebyollin/sdxl-vae-fp16-fix` instead of SDXL's own bundled VAE, plus
`pipe.enable_vae_slicing()`. Found via a real crash: SDXL's default VAE is NaN-unstable in fp16, so
`diffusers` silently upcasts it to float32 to compensate, which doubles the VAE's memory footprint
at exactly the moment it decodes a full image -- enough to push an 8GB card into
`CUDA error: out of memory`, even though the diffusion loop itself completes fine. The fp16-fix VAE
is numerically stable in fp16 and never needs that upcast.

## Validated

Manually, end-to-end, against a real GPU (RTX 4060): both the service standalone (`POST /generate`
directly) and the full chain through `cinemagraph-tool`'s own `POST /generate/image` → job polling →
file download → the web UI's Image tab. See `docs/experiments/2026-07-29-image-generation-backend-choice.md`
for the full account, including why Gemini's hosted API was tried first and reverted, and the VAE
OOM fix above.
