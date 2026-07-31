"""Light sibling module (docs/DESIGN.md sec 3.2's "generation/ (planned,
API-backend case)" row): a thin async client for Google's Gemini image
generation API (the Interactions API, google-genai>=2.16). Deliberately not
under cinemagraph/ (not part of "animate an existing image/video", sec 3.2's
scope test) and not an isolated service like image-generation/ -- google-genai
is a small HTTP client, not a multi-GB local model needing its own container,
so this lives as its own root-pyproject extra instead ("generation", see
pyproject.toml) alongside the same env everything else in src/ shares.

Confirmed directly against the real, installed google-genai==2.16.0 package
and a real API key before writing this -- not from documentation, which gave
two conflicting API shapes and a wrong output MIME type during the first
Gemini attempt (see docs/experiments/2026-07-29-image-generation-backend-choice.md
and docs/experiments/2026-07-31-gemini-adapter.md):

- model: "gemini-3.1-flash-lite-image".
- Output images are JPEG-only: `ImageResponseFormatParam.mime_type` is typed
  `Literal["image/jpeg"]` in the installed package, and a real call confirmed
  it (this repeats the exact finding from the first attempt -- still true).
  Converted to PNG bytes here (via the same opencv already a core dependency
  of the whole repo) so callers of `generate_image` get the same PNG contract
  SDXLAdapter's own output already has, rather than leaking a Gemini-specific
  format detail up through the ImageGenerator port.
- Input reference images accept several formats (png/jpeg/webp/...) but must
  be passed as a file-like object (`io.BytesIO`), not raw `bytes` -- passing
  raw bytes directly raises a `UnicodeDecodeError` deep inside the SDK's own
  JSON serialization, confirmed via a real failed call.
- img2img is multimodal `input`, not a separate endpoint/param: a list of
  `[{"type": "text", ...}, {"type": "image", ...}]` content items.
- `response.output_image.data` is base64-encoded, not raw bytes.
- Gemini's own image-editing API has no equivalent to SDXL's `strength`
  (denoising-strength) knob -- confirmed against `ImageContentParam`'s own
  fields (`data`, `mime_type`, `resolution`, `type`, `uri`; no strength-like
  field). There's nothing to pass even if a caller wanted finer control.
"""
import base64
import io
import mimetypes

import cv2
import numpy as np
from google import genai

MODEL = "gemini-3.1-flash-lite-image"


async def generate_image(
    api_key: str,
    prompt: str,
    *,
    reference_image_bytes: bytes | None = None,
    reference_image_filename: str = "reference.png",
) -> bytes:
    """Text-to-image, or img2img (image-guided edit) when a reference image
    is given. Always returns PNG bytes (see module docstring on why).
    """
    client = genai.Client(api_key=api_key)
    if reference_image_bytes is None:
        request_input = prompt
    else:
        mime_type, _ = mimetypes.guess_type(reference_image_filename)
        request_input = [
            {"type": "text", "text": prompt},
            {
                "type": "image",
                "data": io.BytesIO(reference_image_bytes),
                "mime_type": mime_type or "image/png",
            },
        ]

    response = await client.aio.interactions.create(
        model=MODEL,
        input=request_input,
        response_format={"type": "image", "mime_type": "image/jpeg"},
    )
    if response.output_image is None:
        raise RuntimeError(f"Gemini returned no image (status={response.status!r}).")

    jpeg_bytes = base64.b64decode(response.output_image.data)
    decoded = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
    return cv2.imencode(".png", decoded)[1].tobytes()
