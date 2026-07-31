# Implementing capability ports for generation backends (§3.6/§5.8), image generation first

**Date:** 2026-07-31
**Question:** `docs/DESIGN.md` §3.6 documented a plan (not yet built) to put an
`ImageGenerator` port/registry between `run_image_job` and the image-generation/ service,
justified by image generation's own history of a real backend swap (Gemini -> SDXL). Is
it time to actually build it?

## What was built

- `server/generation_ports.py` -- the `ImageGenerator` Protocol (`generate(prompt,
  **kwargs) -> bytes`), exactly as designed in §3.6, no changes.
- `server/generation_adapters.py` -- `SDXLAdapter`, a direct extraction of the HTTP
  request-building `run_image_job` used to do inline: same `data`/`files` shape, same
  `call_optional_service`/`Settings` dependencies, no behavior change. Takes an injectable
  `call_service` param, same DI shape already used elsewhere in `service.py`.
- `server/generation_registry.py` -- `register_image_generator`/`get_image_generator`/
  `available_image_generators`, the same shape as `effects/base.py`'s `_REGISTRY` (§3.3).
- `server/app.py` registers `"sdxl"` at import time, built from `get_settings()`.
- `run_image_job` (`server/service.py`) now takes an injected
  `image_generator: ImageGenerator | None = None`, defaulting to a fresh
  `SDXLAdapter(settings)` -- built from whatever `Settings` the job itself was called
  with, deliberately *not* pulled from the process-global registry (which is bound to
  `get_settings()`'s cached instance) -- so a test's own custom `Settings(...)` is never
  silently shadowed by the registered one.

## What was deliberately not built

- No `model` field on `POST /generate/image`, no model-selector dropdown in the UI, no
  second adapter (e.g. Gemini). §3.6 already called this out: the registry is
  infrastructure for *when* a second adapter exists, not a reason to build one
  speculatively. Only `"sdxl"` is registered.
- Music/sound-effect generation (`run_music_job`/`run_sound_effect_job`) untouched --
  §3.6's own sequencing says image generation first, proves the pattern, then repeat.

## A real design question resolved while building it: where does the default adapter come from?

The registry stores adapter *instances*, and `SDXLAdapter` needs a `Settings` instance to
be constructed. Real request-time settings are a per-process cached singleton
(`get_settings()`, see `config.py`), which is fine to register against at app-import
time. But `run_image_job` (and its tests) take `settings` as an explicit per-call
argument specifically so tests can construct a custom `Settings(image_generation_url=...)`
without touching `os.environ` or the process-global cache. If `run_image_job`'s default
adapter came from the registry (bound to whatever `Settings` existed at app-import time),
a test's own `Settings(...)` argument would be silently ignored -- a real, easy-to-miss
bug. Resolved by keeping `run_image_job`'s own default construction (`SDXLAdapter(settings)`,
built fresh from the argument it was actually given) independent of the registry
entirely; the registry exists for the future per-request `model` lookup, not for this
default-construction path.

## Verification

- `uv run pytest`: 78 passed (74 existing + 4 new: two `SDXLAdapter` request-shape tests,
  a registry register/get/list roundtrip, and a registry unknown-name error message
  check), 2 deselected (`tests/integration/`, unaffected).
- Two existing `test_api_service.py` tests (`test_image_job_downloads_image_and_registers_
  in_library`, the img2img multipart one) updated to inject a fake `ImageGenerator` via
  the new `image_generator=` parameter instead of a fake `call_service=` -- the seam
  moved from "fake the HTTP call" to "fake the adapter," which is the whole point of the
  port existing.
- Direct import check: `from server.app import app; from server.generation_registry
  import available_image_generators` -> `available_image_generators() == ("sdxl",)`,
  confirming the module-level registration actually runs at real app startup, not just in
  a test's own manual setup.

## Verdict

- [x] `ImageGenerator` port + `SDXLAdapter` + registry built exactly as §3.6 designed,
  no scope creep (no second adapter, no API/UI surface for model selection).
- [x] Zero behavior change to the real `/generate/image` request/response contract --
  pure internal refactor, confirmed by the full test suite passing unchanged in outcome.
- [x] The settings-shadowing risk (registry vs. per-call `settings=`) was caught and
  resolved before it became a real bug, not left as a latent footgun.
- [ ] Music/sound-effect generation ports -- deliberately not started, per §3.6's own
  sequencing (image generation proves the pattern first).
