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

`generate_music` (Lyria 3, added 2026-08-01) talks to the same Interactions
API but bypasses the installed google-genai==2.16.0 SDK's own typed
`interactions.create()` entirely, calling the REST endpoint directly via
`httpx` -- confirmed via a real key that the SDK's own request-building
rejects audio outright (`response_format={"type": "audio", "mime_type":
"audio/mp3"}`, the shape its own type hints and the public docs both show,
400s with "Audio mime_type is not supported in response_format"), and that
this isn't a stale-SDK problem (2.16.0 is also the latest PyPI release; a raw
REST call with that exact body hits the identical 400 -- the API itself
rejects it, not just the client). The actual fix, found by bisecting the
request body: omit `mime_type` entirely, send only `{"type": "audio"}`. See
docs/experiments/2026-08-01-lyria3-adapter.md for the full account.

Further confirmed, against the real API and the public docs
(https://ai.google.dev/gemini-api/docs/music-generation) together:

- model: `"lyria-3-pro-preview"` (not `-clip-preview`, which is hardcoded to
  a fixed ~30s and has no duration control at all -- `-pro-preview` supports
  arbitrary duration via a plain-language instruction in the prompt).
- No separate `lyrics`/`instrumental`/`duration` request fields exist at
  all -- confirmed against the docs and by real calls that only `input`
  (prompt text) and `response_format` do anything. All three are prompt
  *instructions*: lyrics go straight into `input` with `[Verse]`/`[Chorus]`/
  `[Bridge]` structure tags left as literal text (the model reads them, not
  a separate parser); instrumental is a plain sentence like "instrumental
  only, no vocals" (a real call without lyrics auto-classified as
  instrumental on its own, so this is a request, not a guaranteed override --
  unlike ACEStepAdapter's own `"[Instrumental]"` marker, which reliably
  forces it); duration is a plain sentence like "about 90 seconds long" (no
  guarantee of exact length -- Lyria 3 has no numeric duration field to pin
  it to, confirmed by its absence from every response/doc field seen).
- Response shape is NOT symmetric with `generate_image`'s -- there's no
  `response.output_audio`. Audio comes back inside `steps[]`: each element is
  `{"type": "model_output", "content": [...]}`, and the audio-bearing
  content item (`{"type": "audio", "mime_type": "audio/mpeg", "data":
  <base64>}`) can be preceded by other steps (a text step, an internal
  section-marker step) -- callers must scan `steps` for the audio content
  block, not read one fixed attribute.
"""

import base64
import io
import mimetypes

import cv2
import httpx
import numpy as np
from google import genai

MODEL = "gemini-3.1-flash-lite-image"
MUSIC_MODEL = "lyria-3-pro-preview"
_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


async def _post_interaction(api_key: str, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=300.0) as client:
        return await client.post(
            _INTERACTIONS_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )


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


async def generate_music(
    api_key: str,
    prompt: str,
    *,
    lyrics: str = "",
    duration: float | None = None,
    instrumental: bool = False,
    post_interaction=_post_interaction,
) -> bytes:
    """Text-to-music via Lyria 3 Pro. `lyrics`/`duration`/`instrumental` are
    folded into the one `input` prompt string (see module docstring -- Lyria
    3 has no separate fields for any of them), not sent as their own request
    parameters. `post_interaction` is injected (default: the real REST call)
    so tests can fake the httpx boundary without touching the network --
    same DI convention as `run_ffmpeg`/`call_service` elsewhere in this repo.

    Returns raw audio bytes as Lyria 3 itself produced them (`audio/mpeg`,
    confirmed via a real call) -- unlike `generate_image`, no format
    conversion happens here: there's no established PNG-style contract to
    match since ACEStepAdapter/StableAudioAdapter don't normalize their own
    output formats either (run_music_job just writes whatever bytes came
    back to an `output.mp3` path).
    """
    input_text = prompt
    if lyrics:
        input_text += (
            "\n\nUse these lyrics, keeping any [Verse]/[Chorus]/[Bridge]-style "
            f"section tags as written:\n\n{lyrics}"
        )
    if instrumental:
        input_text += "\n\nInstrumental only, no vocals."
    if duration:
        input_text += f"\n\nThe song should be about {duration:.0f} seconds long."

    resp = await post_interaction(
        api_key,
        {
            "model": MUSIC_MODEL,
            "input": input_text,
            "response_format": {
                "type": "audio"
            },  # no mime_type -- see module docstring
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Lyria 3 returned {resp.status_code}: {resp.text}")

    data = resp.json()
    for step in data.get("steps", []):
        for content in step.get("content", []):
            if content.get("type") == "audio":
                return base64.b64decode(content["data"])
    raise RuntimeError("Lyria 3 returned no audio content.")
