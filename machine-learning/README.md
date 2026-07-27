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
being in the core `cinemagraph` image (see the root `Dockerfile`, which only ever installs the
`server` extra, never `ml`). Keeping it as a separate container means:

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

First request (or startup, since the model loads eagerly) downloads the CLIPSeg weights from
the Hugging Face Hub — cached afterward (`HF_HOME`/`/cache` volume in the Dockerfile/compose
entry). CPU-capable but slower; a GPU (CUDA) is picked up automatically if available
(`torch.cuda.is_available()`).

## API

```
GET /health
  200 {"status": "ok", "device": "cuda" | "cpu"}

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

## Validated

Manually, end-to-end, against a real GPU (RTX 4060, using `audio-effect-generation`'s existing
venv which already had `torch`/`transformers` installed): `GET /health` → `{"status": "ok",
"device": "cuda"}`; `POST /segment` on `examples/test_photo.jpg` with `prompt=sky` → real 200,
a 480x320 8-bit grayscale PNG (matching the input's dimensions exactly), pixel values spanning
0–229 (not blank/degenerate). Also validated the full chain through `cinemagraph-tool`'s own
`POST /mask/semantic` with `ML_SERVICE_URL` pointed at the running service → `GET /capabilities`
correctly flipped `semantic_mask` to `true` → the identical PNG bytes came back through the
proxy. See `docs/experiments/2026-07-27-audio-model-serving-research.md` for details.
