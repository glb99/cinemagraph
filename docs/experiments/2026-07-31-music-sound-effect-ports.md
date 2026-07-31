# Extending capability ports to music and sound-effect generation

**Date:** 2026-07-31
**Question:** §3.6's own sequencing said image generation first, then "music and
sound-effect generation follow the same shape once image generation proves it out." Image
generation has now proven it out twice (SDXLAdapter, then GeminiAdapter coexisting). Time
to extract `run_music_job`/`run_sound_effect_job` behind the same port/adapter pattern?

## What was built

- `server/generation_ports.py`: `MusicGenerator` (`generate(prompt, *, lyrics, duration,
  thinking, instrumental=False) -> bytes`) and `SoundEffectGenerator`
  (`generate(prompt, *, duration) -> bytes`).
- `server/generation_adapters.py`: `ACEStepAdapter` and `StableAudioAdapter` -- direct
  extractions of what `run_music_job`/`run_sound_effect_job` used to build inline. ACE-Step's
  own job-queue polling loop (`release_task` -> poll `query_result` -> download) moved into
  `ACEStepAdapter.generate()` unchanged; `MUSIC_POLL_INTERVAL_SECONDS`/`MUSIC_POLL_MAX_ATTEMPTS`
  moved with it.
- `server/generation_registry.py`: two more independent registries
  (`_MUSIC_GENERATORS`/`_SOUND_EFFECT_GENERATORS`), same shape as `_IMAGE_GENERATORS` -- kept
  separate rather than one shared generic registry, since a `MusicGenerator` and an
  `ImageGenerator` aren't genuine peers under §3.3's own bar; only adapters *within* one
  capability are.
- `server/app.py`: registers `"acestep"`/`"stable-audio"` unconditionally at import time
  (same as `"sdxl"`) -- reachability is still checked fresh per request either way.
- `server/service.py`: `run_music_job`/`run_sound_effect_job` now take injected
  `music_generator`/`sound_effect_generator` params, defaulting to a freshly-built
  `ACEStepAdapter(settings)`/`StableAudioAdapter(settings)` -- not a registry lookup by name,
  matching image generation's own first pass before `GeminiAdapter` existed. No `model` field
  added to either route: no second backend exists for either capability yet to justify one.

## A real design question: where does the "[Instrumental]" marker hack belong?

ACE-Step's `/release_task` has no real `instrumental` field -- its server derives instrumental
mode purely by checking if the *lyrics string itself* equals `"[Instrumental]"`/`"[inst]"`
(`acestep/api/server_utils.py`'s `is_instrumental`). The pre-refactor `run_music_job` computed
`effective_lyrics = "[Instrumental]" if instrumental else lyrics` itself, sent that to
ACE-Step, and recorded *that substituted string* in provenance.

Moved the substitution into `ACEStepAdapter.generate()` instead: it's backend-specific wire
knowledge, the same class of thing `GeminiAdapter` already owns (silently ignoring `strength`,
which Gemini's API has no equivalent for). `MusicGenerator`'s own Protocol now takes
`instrumental: bool` directly as a first-class parameter -- a hypothetical second music backend
might support it natively, with no marker hack needed at all.

**Consequence, deliberate:** `run_music_job`'s own provenance now records the lyrics the
*caller* actually submitted, not ACE-Step's internal substitution. Previously, requesting
`instrumental=True` with real lyrics text recorded `"lyrics": "[Instrumental]"` in the asset's
provenance -- an implementation leak, not a meaningful record of what was actually asked for.
Now it correctly records the original lyrics text plus `"instrumental": true`. One existing
test's expectation changed accordingly (and was renamed to describe what it's actually
asserting now -- `test_music_job_provenance_records_submitted_lyrics_not_backend_marker`).

## Test restructuring

The four detailed ACE-Step polling-loop tests (`remote failure`, `times out`, `downloads audio
on success`, `instrumental overrides lyrics`) previously lived in `test_api_service.py`,
driving `run_music_job` directly and monkeypatching `service.MUSIC_POLL_INTERVAL_SECONDS` --
the only way to reach that logic before this refactor, since it lived inline in the job
function. Moved to `test_generation_ports.py`, now driving `ACEStepAdapter.generate()`
directly and monkeypatching `generation_adapters.MUSIC_POLL_INTERVAL_SECONDS` -- testing the
logic where it actually lives now, same principle already applied to `SDXLAdapter`'s own
request-shape tests earlier today.

`test_api_service.py`'s remaining music/sound-effect tests shrank to job-level checks only:
inject a fake `MusicGenerator`/`SoundEffectGenerator`, confirm `run_music_job`/
`run_sound_effect_job` correctly delegate, mark done/error, and record provenance -- the exact
shape `run_image_job`'s own tests already settled into after its first port extraction.

## Verification

- `uv run pytest`: 93 passed (86 existing + 4 ACE-Step adapter tests relocated/adapted + 2
  new registry roundtrip tests for the music/sound-effect registries + 1 new
  `StableAudioAdapter` request-shape test + net test-count change from splitting/renaming the
  music job-level tests), 2 deselected (`tests/integration/`, unaffected -- pure HTTP, unaware
  of this internal split).
- `scripts/golden_check.py`: unaffected (unrelated code path).
- No live-GPU re-verification performed: the actual wire request/response shapes sent to
  ACE-Step and sound-effects/ are byte-for-byte identical to before this refactor, only their
  location in the codebase moved. `tests/integration/test_music_live.py` (built earlier this
  week specifically to catch exactly this class of regression) would fail if that weren't true.

## Verdict

- [x] `MusicGenerator`/`SoundEffectGenerator` ports + `ACEStepAdapter`/`StableAudioAdapter` +
  two more registries built exactly matching image generation's own pattern -- pure internal
  refactor, no behavior change to real request/response wire shapes.
- [x] Found and fixed a real, if minor, provenance-accuracy issue along the way (the
  "[Instrumental]" marker leaking into recorded provenance) by correctly placing that logic
  inside the adapter rather than the job function.
- [x] Test coverage for the polling-loop edge cases preserved, relocated to where that logic
  now actually lives, not lost in the refactor.
- [ ] No `model` field/second adapter for either capability -- deliberately not started, same
  "mechanism present, unconsumed" stance image generation itself began with.
