# machine-learning

Isolated FastAPI service wrapping [CLIPSeg](https://huggingface.co/CIDAS/clipseg-rd64-refined)
(via [`transformers`](https://huggingface.co/docs/transformers/model_doc/clipseg)) for
text-prompted semantic masking ("water", "clouds", "the candle flame") — the model backing
`cinemagraph-tool`'s `POST /mask/semantic`, an alternative to hand-painting a mask PNG for
`cinemagraph from-photo --mask ...`.

Named to match the [Immich](https://github.com/immich-app/immich/tree/main/machine-learning)
convention for this exact shape of split (core app + a separate, optional service for heavy
vision-model work) — not called `ml_sidecar` because this is a standalone container/service
with its own lifecycle, not a sidecar in the strict same-pod sense that term implies in
Kubernetes.

## Why a separate service

`CLIPSeg` needs `torch` + `transformers` — multiple GB of dependencies that have no business
being in the core `cinemagraph` image. The root project's own `pyproject.toml` has no `torch`
dependency anywhere, not even as an unused optional extra — this is the only place those
packages are declared. Keeping it as a separate container means:

- The core image stays small and fast to build regardless of whether anyone ever uses semantic
  masking.
- The core API works standalone with this feature simply reporting as unavailable
  (`GET /capabilities` → `{"semantic_mask": false}`, `POST /mask/semantic` → `503`) whenever
  `ML_SERVICE_URL` is unset or the service isn't reachable — checked at request time, never at
  startup.
- The heavy dependency only needs installing/updating/patched for CVEs on machines actually
  running this feature.

CLIPSeg is loaded through `transformers`'s own `CLIPSegProcessor`/`CLIPSegForImageSegmentation`
rather than the `timojl/clipseg` repo directly — the latter isn't a real PyPI package (only
`pip install git+https://...`), unlike `transformers`, which is.

## Running it

```bash
cd machine-learning
uv run uvicorn app:app --port 8004
```

`uv run` auto-creates this directory's own `.venv` and installs from its own `pyproject.toml`
the first time it's called — same `uv`-first workflow as the root project, just a separate
project/lockfile per the isolation-boundary rule (see `docs/DESIGN.md` §3.2/§7). Plain
`pip install . && uvicorn app:app --port 8004` works too if you'd rather manage the venv
yourself.

Or via Docker Compose from the repo root: `docker compose --profile ml up machine-learning`.

First request (or startup, if `PRELOAD=1`) downloads the CLIPSeg weights from
the Hugging Face Hub — cached afterward (`HF_HOME`/`/cache` volume in the Dockerfile/compose
entry). CPU-capable but slower; a GPU (CUDA) is picked up automatically if available
(`torch.cuda.is_available()`).

## API

```
GET /health
  200 {"status": "ok", "device": "cuda" | "cpu",
       "model_loaded": bool, "inflight_seconds": float | null}
  503 {"status": "stuck", ...}  once a request has run past BUSY_TIMEOUT

  Never loads the model -- core's /capabilities polls this, and a health check that
  loaded on demand would defeat the idle TTL entirely.

  inflight_seconds is null when idle, otherwise how long the current request has been
  running. The 503 is deliberately short-lived: the busy watchdog kills the process on
  its next tick, so what a caller sees next is a refused connection, then a restarted
  service. See "Model lifecycle" below.

POST /segment
  multipart/form-data:
    image: <file>
    prompt: <string>   e.g. "water", "clouds", "the candle flame"

  200 OK
  image/png: a grayscale PNG mask, same dimensions as the input image, white = matches
  the prompt / black = doesn't -- the same white-on-black convention
  cinemagraph.mask.load_mask() already expects from a hand-painted mask, so the core
  service can feed the result straight into the existing mask pipeline unmodified.
```

`server/app.py`'s `POST /mask/semantic` implements the client side of this contract (proxies the
same multipart request through, converts an error/timeout from this service into a `503`
rather than propagating a `500`, and returns the PNG bytes unmodified).

## Model lifecycle

| Var | Default | Meaning |
|---|---|---|
| `MODEL_TTL` | `900` | Seconds idle before the model is unloaded and its memory released. `0` = resident forever (Immich's semantics). |
| `PRELOAD` | off | Load at startup instead of on first request. |
| `BUSY_TIMEOUT` | `1200` | Seconds a single request may run before this process assumes it's wedged and exits. `0` disables. |

`MODEL_TTL` handles an *idle* model. `BUSY_TIMEOUT` handles a *stuck* one, which the TTL
structurally cannot — and which used to defeat it outright: a hung request holds the inference
lock forever, and the idle reaper waited on that same lock before unloading, so it blocked
permanently.

Exiting is the recovery because nothing gentler works: a hung inference call never raises into
Python and the worker thread running it can't be cancelled, so killing the process is the only
reliable way to get the memory back. That means the container needs a restart policy
(`restart: unless-stopped`, set in `docker-compose.yml`) or this trades a stuck service for a
missing one.

CLIPSeg is the smallest of the three satellites and runs on CPU here, so this matters less for
this service specifically. It's applied anyway so all three behave identically rather than one
being a special case someone has to remember — the same reasoning as `MODEL_TTL`.

Prior art, and why this rather than a VRAM-aware scheduler:
`docs/experiments/2026-08-21-model-lifecycle-prior-art.md`.

## Validated

Manually, end-to-end, against a real GPU (RTX 4060, using `audio-effect-generation`'s existing
venv which already had `torch`/`transformers` installed): `GET /health` → `{"status": "ok",
"device": "cuda"}`; `POST /segment` on `examples/test_photo.jpg` with `prompt=sky` → real 200,
a 480x320 8-bit grayscale PNG (matching the input's dimensions exactly), pixel values spanning
0–229 (not blank/degenerate). Also validated the full chain through `cinemagraph-tool`'s own
`POST /mask/semantic` with `ML_SERVICE_URL` pointed at the running service → `GET /capabilities`
correctly flipped `semantic_mask` to `true` → the identical PNG bytes came back through the
proxy. See `docs/experiments/2026-07-27-audio-model-serving-research.md` for details.
