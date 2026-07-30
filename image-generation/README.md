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

POST /generate  (multipart/form-data -- not JSON, see the img2img note below)
  prompt: str (required)
  negative_prompt: str = ""
  steps: int = 30
  guidance_scale: float = 7.5
  width: int = 1024           # ignored if `image` is supplied -- see below
  height: int = 1024          # ignored if `image` is supplied -- see below
  seed: int = -1               # -1 for random
  image: file, optional        # reference image -- switches to img2img mode
  strength: float = 0.6        # img2img only: 0=stay close to reference, 1=ignore it
  -> image/png bytes
```

Plain text-to-image when `image` is omitted. When it's supplied (img2img), the diffusion
process starts from a noised version of the reference image instead of pure noise, and the
output's dimensions follow the reference image's own size (`width`/`height` don't apply).

## A note on the VAE

`app.py` loads `madebyollin/sdxl-vae-fp16-fix` instead of SDXL's own bundled VAE, plus
`pipe.enable_vae_slicing()`. Found via a real crash: SDXL's default VAE is NaN-unstable in fp16, so
`diffusers` silently upcasts it to float32 to compensate, which doubles the VAE's memory footprint
at exactly the moment it decodes a full image -- enough to push an 8GB card into
`CUDA error: out of memory`, even though the diffusion loop itself completes fine. The fp16-fix VAE
is numerically stable in fp16 and never needs that upcast.

## img2img and this GPU's VRAM ceiling

`StableDiffusionXLImg2ImgPipeline.from_pipe(pipe)` shares the same loaded weights as the
text-to-image pipeline -- no second copy of the model -- but img2img still does genuinely new
work text-to-image never does (VAE-*encoding* the reference image, not just decoding a result),
and on an 8GB card that's enough to tip into `CUDA error: out of memory` even with shared weights.
Found via real crashes, not assumed safe: the img2img pipeline is built *lazily* (first actual
img2img request, not eagerly at startup) to avoid regressing the plain text-to-image path's own
VRAM headroom, `torch.cuda.empty_cache()` runs after every request, and `enable_attention_slicing()`
is on alongside VAE slicing. Even with all three, img2img on this GPU is best-effort, not
bulletproof -- occasional `CUDA out of memory` on an img2img request, or on a request shortly
after one, is a known, accepted limitation of running this specific feature on an 8GB card,
not a bug to keep chasing. See `docs/experiments/2026-07-30-image-to-image-generation.md`.

## Validated

Manually, end-to-end, against a real GPU (RTX 4060): both the service standalone (`POST /generate`
directly, text-to-image and img2img both) and the full chain through `cinemagraph-tool`'s own
`POST /generate/image` → job polling → file download. See
`docs/experiments/2026-07-29-image-generation-backend-choice.md` for the original text-to-image
build (including why Gemini's hosted API was tried first and reverted, and the VAE OOM fix above),
and `docs/experiments/2026-07-30-image-to-image-generation.md` for the img2img feature and its
VRAM investigation.
