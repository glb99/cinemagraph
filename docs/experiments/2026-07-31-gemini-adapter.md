# Adding a GeminiAdapter: a real second ImageGenerator, coexisting with SDXL

**Date:** 2026-07-31
**Question:** the project owner now has a working, paid Gemini API key. Re-adopt Google's
hosted image API (tried once before, 2026-07-29, and reverted over a forced non-refundable
prepay) -- this time coexisting with local SDXL, letting a request pick which one to use.
Does the §3.6 capability-ports work (built earlier the same day) actually pay off for this?

## Scope, confirmed with the project owner before building

- Coexist with SDXL, user picks per request (not a full replacement).
- Both text-to-image and img2img (reference image), if Gemini's API actually supports it.

## Verifying the real API contract before writing code

Per this project's own established rule (the first Gemini attempt was misled by two
conflicting doc summaries and a wrong output MIME type) -- introspected the actually-
installed `google-genai==2.16.0` package directly, then made real calls against a real key
(sourced from the repo's own gitignored `.env`, never printed/logged):

- `client.aio.interactions.create(model="gemini-3.1-flash-lite-image", input=...,
  response_format={"type": "image", "mime_type": "image/jpeg"})` -- same model/shape as the
  first attempt, still current.
- Output images are still JPEG-only (`ImageResponseFormatParam.mime_type` typed
  `Literal["image/jpeg"]`) -- confirmed via a real call, matching the first attempt's finding.
- **New finding, not hit by the first attempt** (which was text-only): img2img is multimodal
  `input` -- a list of `[{"type": "text", "text": ...}, {"type": "image", "data": ...,
  "mime_type": ...}]` content items, not a separate endpoint or parameter. Confirmed via
  `google.genai.interactions`' own `ContentParam`/`ImageContentParam` type hints, then a real
  call.
- **A real bug found via a real call, not caught by introspection alone**: passing the
  reference image's raw `bytes` as `ImageContentParam`'s `data` field failed with
  `PydanticSerializationError: ... UnicodeDecodeError: 'utf-8' codec can't decode byte 0x89`
  -- the SDK's own serializer assumes `data` is either a string, a path, or a *file-like*
  object (`io.BytesIO`), not raw bytes, despite the type hint listing `bytes`-shaped inputs
  ambiguously. Fixed by wrapping the reference bytes in `io.BytesIO(...)` before passing --
  confirmed working immediately after.
- `response.output_image.data` is base64-encoded (confirmed by decoding and checking the
  real JPEG magic bytes `\xff\xd8\xff`).

## What was built

- `src/generation/__init__.py` (new) -- a light sibling module per §3.2's own predicted
  "generation/ (planned, API-backend case)" tier row: `generate_image(api_key, prompt, *,
  reference_image_bytes=None, reference_image_filename=...) -> bytes`, wrapping the confirmed
  contract above. Always returns PNG bytes (converts Gemini's JPEG-only output via `cv2`,
  already a core dependency of the whole repo, rather than leaking a Gemini-specific format
  detail up through the `ImageGenerator` port every other adapter also implements).
- `pyproject.toml`: new `generation` extra (`google-genai`), deliberately separate from
  `server` -- a deployment that only wants the self-hosted backends never pulls it in (§3.2's
  tier discipline: this is a thin API client, not a multi-GB local model, so no isolated
  service/container is needed the way SDXL/ACE-Step/Stable Audio Open each get one).
- `server/config.py`: `gemini_api_key: str | None` -- not an `OptionalService` (no URL, no
  health-check-able endpoint; it's a hosted API called directly, not a satellite container).
- `server/generation_adapters.py`: `GeminiAdapter`, implementing the same `ImageGenerator`
  port `SDXLAdapter` already does. `generation` is imported *inside* `generate()`, not at
  module level, so importing this file (for `SDXLAdapter`) never requires `google-genai` to
  be installed -- only deployments that actually configure `GEMINI_API_KEY` need the
  `generation` extra synced. `strength` is accepted (interface parity with `SDXLAdapter`) but
  silently ignored -- Gemini's own image API has no equivalent denoising-strength knob,
  confirmed against the real installed package's `ImageContentParam` fields.
- `server/app.py`: registers `"gemini"` at import time, but only `if
  settings.gemini_api_key` -- same "absent -> just don't offer it" degrade every other
  optional capability already follows. `POST /generate/image` gained a `model` form field
  (default `"sdxl"`), validated against `generation_registry.available_image_generators()`
  at the route (422 for an unknown name, same pattern `/render/photo` already uses for
  unknown effects). `GET /capabilities`'s `image_generation` bool is now `true` if *either*
  SDXL health-checks *or* Gemini is configured (no live health check exists for a hosted
  API); a new `image_generation_models` field lists every registered adapter.
- `server/service.py`: `run_image_job` gained a `model: str = "sdxl"` param; its default
  `image_generator` now resolves via `generation_registry.get_image_generator(model)`
  instead of always building `SDXLAdapter(settings)` -- the registry finally does real work
  (per-request model selection), not just "mechanism exists but nothing uses it." Provenance
  now records `model` alongside `prompt`/`strength`.
- `server/ui.py`: Image tab gained a model `<select>`, populated from
  `caps.image_generation_models` and shown only when more than one adapter is registered
  (matching §3.6's original design intent -- no dropdown for a choice of one).
- `Dockerfile`/`docker-compose.yml`: `core`'s image now syncs `--extra generation`
  unconditionally (cheap, no multi-GB weights); `GEMINI_API_KEY` passed through from the
  root `.env` via Compose's own `${VAR}` substitution, same mechanism already used for every
  other secret in this repo.

## Why the registry design (built earlier the same day, before a second adapter existed) held up

`run_image_job`'s signature didn't change in any way a caller had to adapt to beyond adding
one new optional `model` param -- no rewrite of the job function, exactly the payoff §3.6
predicted from the first Gemini/SDXL swap's own cost. The one real design question that
*did* surface, not anticipated in the original plan: adapters aren't fully homogeneous
peers. `GeminiAdapter` silently ignores `strength`; `effects/base.py` solves the same shape
of problem for effects via `allowed_kwargs` + `validation.resolve_effect_kwargs`, but the
generation registry doesn't have an equivalent yet. Deliberately not built now -- the only
divergence found so far is "one adapter ignores an irrelevant kwarg with no wrong-result
risk," not a case where silently accepting an unsupported feature would produce a bad
outcome. Revisit if that stops being true.

## Verification

- `uv run pytest`: 86 passed (78 existing + 4 for `SDXLAdapter`'s own port/registry
  coverage from earlier + new ones this pass: `generation.generate_image` mocked at the
  `google.genai.Client` boundary for both text-to-image and img2img request shapes, its
  no-output-image error path, `GeminiAdapter`'s pass-through, `/generate/image`'s unknown-
  model 422, a registry-backed model-selection test through `run_image_job` directly, and
  one through the real route via `TestClient`), 2 deselected (`tests/integration/`,
  unaffected). None of the new tests hit the real network -- the real API contract above was
  confirmed separately, once, live.
- **Real, unmocked verification against the actual deployed API** (this project's own
  established rigor, not just "tests pass"): started the real FastAPI app locally with the
  real `GEMINI_API_KEY` sourced from `.env`, confirmed `GET /capabilities` reports
  `image_generation_models: ["sdxl", "gemini"]`, then drove two real jobs through the actual
  HTTP route end to end:
  - Text-to-image (`model=gemini`, "a small red apple..."): job completed, downloaded output
    is a real 1408x768 PNG with real pixel variance (std=35, not blank), registered in the
    library with `provenance: {"prompt": ..., "model": "gemini"}`.
  - img2img (`model=gemini`, real reference PNG uploaded): job completed successfully.

## Verdict

- [x] `GeminiAdapter` built and verified working end to end against the real Gemini API,
  both text-to-image and img2img, through the real `/generate/image` route.
- [x] Coexists with SDXL as designed -- per-request `model` selection, no UI/API surface
  change forced on deployments that never configure `GEMINI_API_KEY`.
- [x] One real API-contract bug found and fixed before it could ship (the `io.BytesIO`
  requirement for reference images) -- found by actually calling the API, not by trusting
  the SDK's own type hints.
- [x] `run_image_job`'s signature absorbed a second real adapter with a one-param addition,
  not a rewrite -- the concrete payoff §3.6 was built to provide.
- [ ] Per-adapter capability declarations (e.g. `supports_img2img`, `supports_strength`) --
  deliberately not built; no real need has appeared yet (see rationale above).
