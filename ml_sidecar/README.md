# ml_sidecar (not yet built)

Placeholder for the future CLIPSeg-based semantic-mask feature: given a
photo and a text prompt ("water", "clouds", "the candle flame"), return a
soft mask locating that content — an alternative to hand-painting a mask
PNG for `cinemagraph from-photo --mask ...`.

This directory intentionally has no code or `Dockerfile` yet. The point of
committing this README now, ahead of the implementation, is to reserve the
integration seam in `api/app.py` (`GET /capabilities`, `POST /mask/semantic`)
and in `docker-compose.yml` (the commented-out `ml-sidecar` service) — so
building this later is a matter of filling in this directory and
uncommenting two lines, not redesigning how the core API talks to it.

## Why a separate service

`CLIPSeg` needs `torch` + `transformers` — multiple GB of dependencies that
have no business being in the core `cinemagraph` image (see the root
`Dockerfile`, which only ever installs the `api` extra, never `ml`). Keeping
it as a separate container means:

- The core image stays small and fast to build regardless of whether anyone
  ever uses semantic masking.
- The core API works standalone with this feature simply reporting as
  unavailable (`GET /capabilities` → `{"semantic_mask": false}`,
  `POST /mask/semantic` → `503`) whenever `ML_SIDECAR_URL` is unset or the
  sidecar isn't reachable — checked at request time, never at startup.
- The heavy dependency only needs installing/updating/patched for CVEs on
  machines actually running this feature.

## Intended contract

```
POST /segment
  multipart/form-data:
    image: <file>
    prompt: <string>   e.g. "water", "clouds", "the candle flame"

  200 OK
  multipart or application/octet-stream: a grayscale PNG mask, same
  dimensions as the input image, white = matches the prompt / black =
  doesn't -- the same white-on-black convention cinemagraph.mask.load_mask()
  already expects from a hand-painted mask, so the core service can feed
  the result straight into the existing mask pipeline unmodified.

GET /health
  200 OK once the model is loaded and ready to serve requests.
```

`api/app.py`'s `POST /mask/semantic` already implements the client side of
this contract (proxies the same multipart request through, converts a
sidecar error/timeout into a `503` rather than propagating a `500`) — see
that function's docstring for the exact behavior.

## Sketch of the eventual implementation

(Not built yet — this is a starting point for whoever picks this up.)

```python
from transformers import CLIPSegProcessor, CLIPSegForImageSegmentation
import torch

processor = CLIPSegProcessor.from_pretrained("CIDAS/clipseg-rd64-refined")
model = CLIPSegForImageSegmentation.from_pretrained("CIDAS/clipseg-rd64-refined")

def segment(image_rgb, prompt: str) -> np.ndarray:
    inputs = processor(text=[prompt], images=[image_rgb], return_tensors="pt")
    with torch.no_grad():
        logits = model(**inputs).logits
    probs = torch.sigmoid(logits[0]).numpy()
    return cv2.resize(probs, (image_rgb.shape[1], image_rgb.shape[0]))  # 0..1 heatmap
```

Wrap that in a small FastAPI app (`ml_sidecar/app.py`), add a `Dockerfile`
based on a CUDA or CPU-only PyTorch image depending on target hardware, and
a `pyproject.toml` with the `ml` extra's dependencies
(`torch`, `transformers`) as its actual requirements (not optional there —
this service exists *only* to run them).
