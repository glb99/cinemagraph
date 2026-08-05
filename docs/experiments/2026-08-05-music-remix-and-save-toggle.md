# Music remix (cover/repaint/reference-audio) + per-generation "save to library" toggle

**Date:** 2026-08-05

## What was built

Two independent additions, planned together via a Plan-Mode-approved design
(subagent-validated for the remix architecture decision, see the plan's own
Context section for the reasoning):

1. **Remix support** -- `MusicGenerator.remix()`, a second protocol method
   (not new `generate()` kwargs -- Lyria 3 has no source-audio-conditioning
   mode at all to gracefully degrade to, unlike `thinking`/`strength`).
   `ACEStepAdapter.remix()` implements `cover` (restyle an existing song),
   `repaint` (regenerate a time range), and reference-audio style transfer
   (independent of task_type) via ACE-Step's real `/release_task` multipart
   contract. `Lyria3Adapter.remix()` raises `NotImplementedError` --
   `supports_remix`/`available_music_remix_generators()`/
   `/capabilities`' `music_remix_models` keep this unreachable from the UI
   and rejected server-side (422) if hit directly.
2. **`save_to_library`** -- a `Form(True)`-defaulted opt-out on all six
   generation routes. Needed zero `service.py` changes: every job function
   already accepted `library_kind: str | None = None` and already treated
   `None` as "skip registration" -- purely a routing + UI checkbox change.

## Real verification (not just unit tests)

`uv run pytest`: 140 passed (up from 122 before this work), including new
coverage in `test_generation_ports.py` (adapter-level multipart shape per
task_type), `test_api_service.py` (`run_music_job` routes to `remix()` vs
`generate()` correctly), and `test_api_smoke.py` (route-level 422s, and real
successful requests via registry-registered fake adapters -- the same
pattern `test_generate_image_uses_requested_model` already established).

Real deployment, not mocked, after rebuilding `core` and confirming
`/capabilities` reported `"music_remix_models":["acestep"]` (Lyria 3
correctly excluded):

1. Generated a real 15s source song (`thinking=false` for speed --
   see `2026-08-01-acestep-poll-timeout.md` for why `thinking=true` is slow),
   confirmed `status: done`.
2. Real `task_type=cover` request against that asset's real id --
   `prompt="heavy metal version with distorted electric guitars and
   aggressive drums"`, `cover_strength=0.5` -- completed successfully
   end-to-end through the actual `ACEStepAdapter.remix()` multipart path,
   not a raw script. Both files sent to the project owner directly for a
   listen (pending their confirmation the result is audibly related to the
   source but restyled).
3. Real `save_to_library=false` request through the live `/generate/music`
   route: job completed (real output file produced), library asset count
   unchanged (111 before, 111 after) -- confirmed live, not just via the
   `asset_library.list_assets() == []` unit test.
4. Confirmed the served `/` HTML actually contains the new UI elements
   (`music-task-type`, `music-source-picker`, `music-reference-picker`,
   `music-save-to-library`, `assemble-save-to-library`) -- not just that
   `ui.py`'s Python source has the right strings.

## Not independently live-tested this round

`repaint` and plain-`text2music`-with-`reference_audio` weren't run against
the live server separately -- both are unit-tested precisely (exact
`data`/`files` shape asserted in `test_generation_ports.py`) and share the
identical request-building code path `cover` already proved live (same
`remix()` method, same multipart branch, only which fields are populated
differs). Flagged as a real gap, not glossed over -- worth a live check
before relying on either in practice, same rigor this project applies
everywhere else.

## Verdict

- [x] `uv run pytest` -- 140 passed, no regressions.
- [x] Real cover remix through the actual deployed adapter, not a script --
  output sent to the project owner for a listen.
- [x] `save_to_library=false` confirmed live (library count unchanged).
- [x] `model=lyria3` confirmed excluded from `music_remix_models` in a real
  `/capabilities` response.
- [ ] `repaint`/reference-style-transfer not independently live-tested --
  unit-tested only, real verification still pending.
