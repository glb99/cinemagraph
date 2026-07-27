# sound-effects

Isolated FastAPI service wrapping [Stable Audio Open](https://huggingface.co/stabilityai/stable-audio-open-1.0)
(via [`stable-audio-tools`](https://github.com/Stability-AI/stable-audio-tools)) for text-prompted
ambient/SFX generation — the model backing `cinemagraph-tool`'s `POST /generate/sound-effect`.

Generation logic is a direct port of the proven-working
`audio-effect-generation/generate_rain_stableaudio.py` experiment (see that project's `RESEARCH.md`
for why a serving layer wasn't worth it *there*, as a standalone script — it is here, since this
service has a real caller: `cinemagraph-tool`'s own API). The only thing this service adds is loading
the model once at startup instead of once per call.

## Why a separate service

Same reasoning as `machine-learning/` (see `docs/DESIGN.md` §3.2/§5.7): `torch`/`stable-audio-tools`
are multi-GB dependencies with no business in the core `cinemagraph-tool` image, and text-to-audio
generation isn't part of what `cinemagraph` (the animate-a-photo library) does — it's a separate
capability that happens to serve the same ambient-video goal.

## Running it

```bash
cd sound-effects
uv run uvicorn app:app --port 8003
```

`uv run` auto-creates this directory's own `.venv` and installs from its own `pyproject.toml`
the first time it's called — same `uv`-first workflow as the root project, just a separate
project/lockfile per the isolation-boundary rule (see `docs/DESIGN.md` §3.2/§7). Plain
`pip install . && uvicorn app:app --port 8003` works too if you'd rather manage the venv
yourself.

Or via Docker Compose from the repo root: `docker compose --profile audio up sound-effects`.

First request (or startup, since the model loads eagerly) downloads ~5GB of weights from the
Hugging Face Hub — cached afterward (`HF_HOME`/`/cache` volume in the Dockerfile/compose entry).
CPU-capable but slow; a GPU (CUDA) is picked up automatically if available (`torch.cuda.is_available()`).

## API

```
GET /health
  200 {"status": "ok", "device": "cuda" | "cpu"}

POST /generate
  {"prompt": "...", "duration": 10.0, "steps": 100, "cfg_scale": 7.0, "seed": -1}
  -> audio/wav bytes

  duration: seconds, up to ~47s (the model's fixed latent window; longer requests get trimmed)
  steps: diffusion sampling steps (fewer = faster, lower quality)
  seed: -1 for random
```

## Validated

Manually, end-to-end, against a real GPU (RTX 4060) with the already-cached model weights: both the
service standalone (`POST /generate` directly) and the full chain through `cinemagraph-tool`'s own
`POST /generate/sound-effect` → job polling → file download. See
`docs/experiments/2026-07-27-audio-model-serving-research.md` and the follow-up session for details.
Not yet validated: the Docker build itself (Docker wasn't running in the validating session) — the
Python-level logic is proven, the containerization is not.
