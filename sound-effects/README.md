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

**`stabilityai/stable-audio-open-1.0` is a gated model** — the download needs an authenticated,
license-accepted Hugging Face token, or it fails with a 401 at startup. Accept the license at
https://huggingface.co/stabilityai/stable-audio-open-1.0, create a token at
https://huggingface.co/settings/tokens, then set `HF_TOKEN` in the environment (via Docker Compose:
a `.env` file at the repo root with `HF_TOKEN=...`, gitignored, never committed).

## API

```
GET /health
  200 {"status": "ok", "device": "cuda" | "cpu",
       "model_loaded": bool, "inflight_seconds": float | null}
  503 {"status": "stuck", ...}  once a generation has run past BUSY_TIMEOUT

  Never loads the model -- core's /capabilities polls this, and a health check that
  loaded on demand would defeat the idle TTL entirely.

  inflight_seconds is null when idle, otherwise how long the current generation has
  been running. The 503 is deliberately short-lived: the busy watchdog kills the
  process on its next tick, so what a caller sees next is a refused connection, then a
  restarted service. See "Model lifecycle" below.

POST /generate
  {"prompt": "...", "duration": 10.0, "steps": 100, "cfg_scale": 7.0, "seed": -1}
  -> audio/wav bytes

  duration: seconds, up to ~47s (the model's fixed latent window; longer requests get trimmed)
  steps: diffusion sampling steps (fewer = faster, lower quality)
  seed: -1 for random
```

## Model lifecycle

| Var | Default | Meaning |
|---|---|---|
| `MODEL_TTL` | `900` | Seconds idle before the model is unloaded and its VRAM released. `0` = resident forever (Immich's semantics). |
| `PRELOAD` | off | Load at startup instead of on first request. |
| `BUSY_TIMEOUT` | `1200` | Seconds a single generation may run before this process assumes it's wedged and exits. `0` disables. |

`MODEL_TTL` handles an *idle* model. `BUSY_TIMEOUT` handles a *stuck* one, which the TTL
structurally cannot — and which used to defeat it outright: a hung generation holds the
generation lock forever, and the idle reaper waited on that same lock before unloading, so it
blocked permanently and this service kept the card exactly when the other satellites needed it.

Exiting is the recovery because nothing gentler works. A hung CUDA call never raises into
Python, the worker thread running it can't be cancelled, and `torch.cuda.empty_cache()` can't
release memory the wedged interpreter still owns. Killing the process is what hands the card
back to the driver — so the container needs a restart policy (`restart: unless-stopped`, set in
`docker-compose.yml`) or this trades a stuck service for a missing one.

Prior art, and why this rather than a VRAM-aware scheduler:
`docs/experiments/2026-08-21-model-lifecycle-prior-art.md`.

## Validated

Manually, end-to-end, against a real GPU (RTX 4060): both the service standalone (`POST /generate`
directly) and the full chain through `cinemagraph-tool`'s own `POST /generate/sound-effect` → job
polling → file download → the web UI's Sound effects tab, playing back real generated audio in a
real browser (`readyState: 4`, correct duration). See
`docs/experiments/2026-07-27-audio-model-serving-research.md` and
`docs/experiments/2026-07-29-sound-effects-docker-gpu-verification.md` for details, including two
real bugs found and fixed in the process (a missing GPU `deploy:` block, and a `torchaudio.save`
failure from a `torchcodec` backend dependency the image didn't have). The Docker build and GPU
passthrough are now validated too, not just the Python-level logic.
