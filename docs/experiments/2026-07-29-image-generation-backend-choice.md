# Image generation: Gemini hosted API, then reverted to local SDXL

**Date:** 2026-07-29
**Question:** `docs/DESIGN.md` §5.2 left image generation's backend "genuinely undecided" --
"first implementation = whichever external API is cheapest to stand up." Which one, and does
it actually work end to end?

## Attempt 1: Gemini's native image models

### Research before building

Checked current (2026) options rather than assuming: local SDXL (free, runs on this machine's
8GB RTX 4060) vs. hosted frontier models. Found that SDXL is rated behind Flux 2, Imagen 4, and
Midjourney v7 on photorealism "by a meaningful margin" in 2026, while Google's Imagen 4 Fast
priced at $0.02/image -- cheap enough that cost looked like a non-factor. Decided hosted was the
better call given the quality gap and trivial apparent cost.

Then found Imagen models are being shut down 2026-08-17 -- weeks away. Google's documented
replacement is Gemini's native image generation ("Nano Banana", `gemini-3.1-flash-lite-image`
being the cheapest tier, ~$0.03/image at 1K resolution).

### Verifying the actual API shape before writing code

Two `WebFetch` calls against Google's own docs gave inconsistent answers -- one described
`client.models.generate_content`, another described a newer `client.interactions.create`
("Interactions API"). Rather than trust either, introspected the actually-installed
`google-genai==2.14.0` package directly:

```python
from google import genai
c = genai.Client(api_key='fake-key-for-introspection')
hasattr(c.aio, 'interactions')  # True
inspect.signature(c.aio.interactions.create)  # confirmed real, generic **body signature
```

and checked the real Pydantic response model fields (`google.genai.interactions.Interaction`,
`ImageContent`) directly rather than trust a doc summary's claimed `interaction.output_image.data`
convenience property -- confirmed `output_image` is a real `ImageContent | None` field, and
`ImageContent.data` really is the base64 image string. The doc fetch wasn't hallucinating that
part, just mischaracterizing it as a "property" rather than a plain field.

### Built

- `src/generation/__init__.py`: `generate_image(prompt, *, api_key) -> bytes`, async, calling
  `client.aio.interactions.create(model="gemini-3.1-flash-lite-image", input=prompt,
  response_format={"type": "image", "mime_type": "image/png"})`.
- `server/config.py`: `gemini_api_key` field.
- `server/service.py`: `run_image_job`, DI'd `generate_fn` parameter (matching the
  `call_service`/`render_fn` pattern from the same day's earlier DI work).
- `server/app.py`: `POST /generate/image`, `image_generation` in `/capabilities` (checked as
  `bool(settings.gemini_api_key)` -- no free health-check to make against a paid hosted API).
- Web UI: sixth "Image" tab, same shape as Music/Sound effects.
- Tests: mocked `generate_fn`, all passing.

### First real request: wrong MIME type

`response_format.mime_type: "image/png"` was rejected outright:

```
Error code: 400 - {'error': {'message': "The value 'image/png' is not supported for
'response_format.mime_type'. Supported values: 'image/jpeg'."}}
```

Fixed by switching to `image/jpeg` (and the output filename to `.jpg`).

### Second real request: the actual blocker

```
Error code: 429 - {'error': {'message': 'You exceeded your current quota... limit: 0...
Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests'}}
```

`limit: 0` -- not a small free allowance used up, a genuinely zero free-tier allocation for this
model. The user tried to add billing and was told Google AI Studio now requires a minimum $10
prepay, non-refundable if unused within a year -- a policy that took effect 2026-03-23 for new
accounts, confirmed via search (not in any pricing page read earlier). Vertex AI (Google Cloud's
separate API surface) offers standard postpaid billing with no forced prepay, but needs a GCP
project + billing account and a different auth flow (service credentials, not a bare API key) --
real additional setup, not a config toggle.

### Decision

User chose not to take on the Vertex AI setup or the forced prepay. Reverted the entire hosted
attempt -- not disabled, removed: `src/generation/` deleted, `google-genai` dependency removed,
`gemini_api_key`/`GEMINI_API_KEY` removed from config and compose, `run_image_job` rewritten.
Half of a false start left in the tree (e.g. an unused package still importable, a dead config
field) is worse than a clean revert -- confusing for anyone (including a future session) reading
the code without this context.

## Attempt 2: Local Stable Diffusion XL

### Research

Re-checked current 2026 local-model VRAM/quality data specifically for an 8GB card: SDXL is the
consensus best fit (~7GB used natively on an RTX 4060, no quantization needed, ~8s for a 1024px
image), ahead of SD 1.5 (older, lower quality) and behind Flux (needs quantization to fit 8GB,
more setup risk for uncertain gain).

### Built

Exact shape of `sound-effects/`, the established pattern for an isolated single-purpose model
service:

- `image-generation/{pyproject.toml,Dockerfile,app.py,README.md}` -- `diffusers`'
  `StableDiffusionXLPipeline`, eager-loaded at startup, `POST /generate {prompt, ...} ->
  image/png`, `GET /health`.
- `docker-compose.yml`: new `image-generation` service, GPU `deploy:` block included from the
  start this time (sound-effects/ initially shipped without one and that gap had to be found and
  fixed later the same day -- no reason to repeat that here).
- `server/config.py`/`service.py`/`app.py`: `run_image_job` rewritten to the exact
  `run_sound_effect_job` shape (`call_optional_service`, `IMAGE_GENERATION_URL`,
  `image_generation_service` `OptionalService`), replacing the Gemini-specific version.
- Tests updated to the `call_service`-fake pattern instead of a `generate_fn`-fake, matching
  `run_sound_effect_job`'s own tests.

### Result

Built and started clean (`docker compose --profile image build/up`); `GET /health` confirmed
`{"device": "cuda"}`. First real end-to-end request through `core` failed with a generic
"unavailable: unreachable or returned an error" -- but the `image-generation` container's own log
showed the same request completing with `200 OK` in ~19s. Isolated by calling both `/health` and
`POST /generate` directly from inside the `core` container (bypassing the host-published ports
entirely, to rule out a host-vs-container-network difference) -- both succeeded immediately.
Concluded this was a one-off cold-start race (the request landed right as the container had *just*
become healthy) rather than a real bug, and confirmed by simply retrying the full
`POST /generate/image` → job polling flow, which succeeded cleanly.

Verified the actual output, not just job status: downloaded the real file and checked it with
OpenCV -- 1024x1024, real pixel variance (std=64, not blank). Sent the actual generated image to
the user. Then repeated through the real web UI: clicked the Image tab (now visible, confirming
`GET /capabilities` correctly reports `image_generation: true`), submitted a real prompt through
the real form, and read the resulting `<img>` element's own `complete`/`naturalWidth`/
`naturalHeight` properties directly after the job finished (`true`/`1024`/`1024`) -- the same
rigor used earlier in this project for verifying video/audio playback, not just "the API returned
bytes." Confirmed both generated images appeared correctly in the Library tab, tagged `["image"]`
with `provenance: {prompt}` matching what was actually requested.

## Verdict

- [x] Adopted -- `image-generation/` (local SDXL), wired the same way `sound-effects/` is.
- [x] Reverted -- the Gemini/hosted-API attempt, cleanly removed rather than left disabled.

## Follow-up: VAE decode OOM under real use (same day)

Found through the user actually running the service, not in testing: a real request completed the
full diffusion loop (30/30 steps logged) then crashed decoding the result into pixels --
`torch.AcceleratorError: CUDA error: out of memory`, right after a
`FutureWarning: upcast_vae is deprecated` line in the same traceback.

Root cause: SDXL's own bundled VAE produces NaN values in fp16, so `diffusers` silently upcasts it
to float32 to compensate -- doubling the VAE's memory footprint at exactly the point it's decoding
a full 1024x1024 image, stacked on top of whatever the UNet + text encoders already used on an
8GB card. Confirmed against current guidance (search, not assumption) that this is a known,
well-documented SDXL-on-8GB failure mode with a standard fix: swap in
`madebyollin/sdxl-vae-fp16-fix`, a precision-corrected VAE checkpoint that's numerically stable in
fp16 and never needs the upcast, plus `pipe.enable_vae_slicing()` as an independent second safety
margin.

Applied both to `image-generation/app.py`. Rebuilt, force-recreated the container (a plain
`docker compose up -d` didn't reliably show as recreated even after a real image rebuild --
explicit `stop` + `rm -f` + `up -d` was used instead to be certain the new code was actually
running, not just assumed from ambiguous `docker inspect` output). Re-ran the *exact* prompt and
settings that had OOM'd before: succeeded cleanly, no crash. Downloaded and validated the actual
output (1024x1024, real pixel variance, not corrupted) -- the first validation attempt showed
`libpng error: Read Error`, traced to the verification `curl` call's own `-m 60` timeout cutting
the download short mid-stream, not a bug in the service; re-ran without that constraint and the
file was intact.

### Verdict (follow-up)

- [x] Adopted -- `madebyollin/sdxl-vae-fp16-fix` + `enable_vae_slicing()` in `image-generation/app.py`.

## Notes

Every `docker compose` invocation across both attempts was explicitly torn down afterward
(containers removed, host-side processes checked for stragglers) before moving on, including one
leftover `sound-effects` container from earlier the same session that `docker compose down`
(without a matching `--profile`) had silently left behind -- caught by checking `docker ps -a`
rather than assuming a plain `down` was sufficient.
