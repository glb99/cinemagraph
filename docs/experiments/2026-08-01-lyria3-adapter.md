# Lyria 3 as a second `MusicGenerator` adapter: verified working, not yet built

**Date:** 2026-08-01
**Question:** does Google's newly-documented Lyria 3
(https://ai.google.dev/gemini-api/docs/music-generation) actually work as a one-shot
`prompt -> finished audio file` call, unlike Lyria RealTime (rejected
2026-07-31, see `2026-07-31-lyria-music-rejected.md`, for being a live-streaming-only API
with no natural clip boundary)?

## What was checked, against the real API (not just docs)

Same discipline as every other backend decision in this project: real key, real calls,
docs treated as a hypothesis to confirm or contradict, not a source of truth on their own.

**First attempt, following the docs' own example almost verbatim, failed:**

```python
response = await client.aio.interactions.create(
    model="lyria-3-clip-preview",
    input="cinematic atmospheric piano music, slow and reflective, soft ambient pads underneath, film-score quality",
    response_format={"type": "audio", "mime_type": "audio/mp3"},
)
```

```
google.genai._gaos.lib.compat_errors.BadRequestError: Error code: 400 -
{'error': {'message': 'Audio mime_type is not supported in response_format.', 'code': 'invalid_request'}}
```

Confirmed this wasn't an installed-SDK-is-stale problem: `google-genai==2.16.0` (already
installed) is also the latest version on PyPI, and a raw REST call to the documented
endpoint (`POST https://generativelanguage.googleapis.com/v1beta/interactions`), bypassing
the SDK entirely, hit the exact same `400` with the exact same message. The rejection is
coming from the API itself, not a client-side typing/validation bug.

**Root cause, found by bisecting the request body:** the `mime_type` field inside
`response_format` is what triggers the rejection -- dropping it and sending only
`{"type": "audio"}` (no `mime_type` key at all) returns `200` with real audio. Tried three
shapes total:

| `response_format` sent | Result |
|---|---|
| `{"type": "audio", "mime_type": "audio/mp3"}` (docs' own example) | `400 Audio mime_type is not supported in response_format.` |
| `{"response_modalities": ["AUDIO"]}` (guessed alternate shape) | `400 The value 'AUDIO' is not supported for 'response_modalities[0]'.` |
| `{"type": "audio"}` (no `mime_type`) | **`200`, real audio returned** |

## Confirmed response shape (differs from the docs and from Gemini images)

Real output, `model="lyria-3-clip-preview"`, prompt: *"cinematic atmospheric piano music,
slow and reflective, soft ambient pads underneath, film-score quality"* (no lyrics, no
explicit instrumental flag):

```json
{
  "id": "...", "status": "completed", "model": "lyria-3-clip-preview",
  "steps": [
    {"type": "model_output", "content": [{"type": "text", "text": "<instrumental>"}]},
    {"type": "model_output", "content": [{"type": "audio", "mime_type": "audio/mpeg", "data": "<base64>"}]}
  ]
}
```

- Audio is **not** on a top-level `output_audio` field the way images have
  `response.output_image` -- it's `content[0].data` inside the *last* `model_output` step.
  A caller has to scan `steps` for the audio-typed content block, not just read one fixed
  attribute.
- Lyria 3 auto-classified the untagged, lyric-less prompt as instrumental on its own (a
  `"<instrumental>"` text step precedes the audio step) -- no explicit flag was passed.
  Whether an explicit instrumental/vocal control exists wasn't tested here.
- Real MP3 (`audio/mpeg`), ~744KB decoded, played back correctly -- saved to disk and sent
  to the project owner for a listen, not just checked for "some bytes came back."
- The installed `google-genai==2.16.0` SDK's own typed `interactions.create()` wrapper
  can't be used as-is for this today -- it builds the exact `mime_type`-including request
  shape that the API rejects. Either the raw REST endpoint has to be called directly
  (as this test did, via `httpx`), or a future SDK release needs to fix its own
  `response_format` construction for audio.

## What was *not* tested (open unknowns before building a real adapter)

Unlike the confirmed pieces above, these are genuinely unknown, not assumed -- flagging
explicitly rather than guessing, matching how this project always verifies before coding
(see the Lyria RealTime and Gemini-image episodes for the same pattern):

- **`lyrics`** -- the docs mention "custom lyrics with structural tags like `[Verse]`,
  `[Chorus]`, `[Bridge]`" as an input, but the exact request shape for passing them
  (a separate field? embedded in `input` text?) wasn't tried.
- **`instrumental` as an explicit flag** -- only auto-classification from an untagged
  prompt was observed; whether there's a real toggle (the way ACEStepAdapter's
  `"[Instrumental]"` lyrics-marker hack works) is unconfirmed.
- **`duration`** -- `lyria-3-clip-preview` is documented as a fixed ~30s clip length;
  `lyria-3-pro-preview` (full songs, "a couple of minutes") wasn't called at all this
  session. `MusicGenerator`'s port has a `duration: float` parameter ACEStepAdapter
  actually honors -- unclear yet whether Lyria 3 has an equivalent knob or only a
  clip-vs-pro model choice.
- **`thinking`** -- ACE-Step-specific (5Hz LM chain-of-thought reasoning); Lyria 3 has no
  obvious equivalent, would likely be accepted-and-ignored the same way GeminiAdapter
  already does with `strength` for images.

## Follow-up: `lyrics`/`instrumental`/`duration` resolved, `Lyria3Adapter` built

Re-read the public docs (https://ai.google.dev/gemini-api/docs/music-generation) in full
rather than guessing at request shapes for the three open unknowns above -- the docs
themselves were reliable here (unlike the `response_format` example, which wasn't):
Lyria 3 has **no separate fields at all** for lyrics, instrumental, or duration -- all
three are natural-language instructions folded into the single `input` prompt string.
Confirmed with one more real call (`lyria-3-pro-preview`, `[Intro]`/`[Build]`/`[Outro]`
structure tags + an explicit "instrumental only, no vocals" sentence) -- got back a real,
much longer file (2.4MB vs. the clip model's ~744KB), consistent with Pro's "couple of
minutes" default duration.

`Lyria3Adapter` (`server/generation_adapters.py`) and `generation.generate_music`
(`generation/__init__.py`) are now built against this, registered in
`generation_registry` as `"lyria3"` alongside `"acestep"` (gated on `GEMINI_API_KEY`, same
as `"gemini"` for images), and wired through `run_music_job`'s new `model` parameter,
`/generate/music`'s `model` form field, `/capabilities`' `music_generation_models`, and
the web UI's Music tab model dropdown -- the exact same shape `GeminiAdapter` established
for images. Verified end-to-end through the real adapter (not a standalone script) against
the live API: a real 1.5MB clip came back through `Lyria3Adapter.generate()` called via
`generation_registry.get_music_generator("lyria3")`.

`instrumental` remains a *request*, not a guaranteed override the way ACEStepAdapter's
`"[Instrumental]"` marker is -- there's no marker to substitute since no dedicated field
exists, so the adapter's docstring flags this explicitly rather than silently overpromising.

## Verdict

- [x] Confirmed via real API + real raw REST calls, not docs alone, that Lyria 3's
  one-shot `interactions.create()` route works for audio -- contrary to what the
  documented `response_format` example itself would produce (it 400s).
- [x] Found and worked around the actual bug: omit `mime_type` from `response_format`.
- [x] Located the real response shape (audio inside `steps[].content[]`, not a top-level
  field) by inspecting a real response, not assuming symmetry with the image API.
- [x] `lyrics`/`instrumental`/`duration` mapping resolved by reading the full public docs
  (all three are prompt text, no separate fields) and confirmed with one more real call.
- [x] `Lyria3Adapter` built, registered, wired through the API/UI, and verified
  end-to-end against the live API through the actual adapter path.
