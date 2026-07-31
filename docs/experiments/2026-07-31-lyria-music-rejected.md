# Lyria as a second MusicGenerator adapter: investigated and rejected

**Date:** 2026-07-31
**Question:** with a working, paid Gemini API key already in hand (from the image-generation
work) and `MusicGenerator`/`generation_registry` freshly built for exactly this purpose, is
Google's Lyria a good second adapter for `POST /generate/music`, alongside `ACEStepAdapter`?

## What was checked, against the real API (not docs)

Same discipline as the Gemini image work: introspected the installed `google-genai==2.16.0`
package directly, then made real calls with a real key.

`google.genai.interactions`'s own type hints suggested the same one-shot
`client.aio.interactions.create(...)` call that worked for Gemini images would also work for
audio: `AudioResponseFormatParam` lists `mime_type: Literal["audio/mp3", "audio/ogg_opus",
"audio/l16", "audio/wav", "audio/alaw", "audio/mulaw"]`, and `Interaction` has an
`output_audio` field. Tried `model="lyria-3-clip-preview"` with `response_format={"type":
"audio", "mime_type": <each of the six>}` -- every single one failed identically:

```
Error code: 400 - {'error': {'message': 'Audio mime_type is not supported in
response_format.', 'code': 'invalid_request'}}
```

Not a wrong-MIME-type problem (unlike the earlier Gemini-image episode, where switching
`image/png` → `image/jpeg` fixed it) -- the *entire* one-shot request/response route appears
unavailable for audio on this model, contrary to what the SDK's own type hints imply.

The only actually-working Lyria access path, confirmed by inspecting `client.aio.live.music`:

```
client.aio.live.music.connect(*, model: str) -> AsyncIterator[AsyncMusicSession]
# session methods: set_weighted_prompts, set_music_generation_config,
# play, pause, stop, reset_context, receive (-> AsyncIterator[LiveMusicServerMessage]), close
```

`connect.__doc__` literally says `"[Experimental] Connect to the live music server."` This is
a persistent WebSocket session for live, continuously-steerable music generation (weighted
text prompts you can update mid-stream, a running config you can tweak), not a "submit a
prompt, get back a finished audio file" call.

## Why this was rejected, not just "harder to build"

The mismatch isn't only complexity -- it's a different use case than what `/generate/music`
actually does:

- **No natural clip boundary.** The live session streams PCM continuously until you call
  `stop()`; there's no `duration` parameter or "generate N seconds and finish" semantics.
  Getting a fixed-length clip out would mean arbitrarily cutting a continuous stream, which
  would very likely produce an abrupt, non-musical ending -- unlike ACE-Step's actual
  finished, structured pieces.
- **No lyrics/vocals control found** -- Lyria's live-music API appears built around
  instrumental/abstract weighted prompts, not the prompt+lyrics+instrumental shape
  `MusicGenerator`'s port (and this project's whole music UI) is built around.
- **`[Experimental]`** per the SDK's own docstring -- a real API-stability risk on top of the
  use-case mismatch.

Lyria RealTime is a good fit for a genuinely different feature (live background music that
reacts to user input, e.g. during interactive playback) -- not a fit for this project's
current one-shot, job-queue-based `/generate/music` route.

## Decision

Do not build a `LyriaAdapter`. `ACEStepAdapter` remains the only registered `MusicGenerator`.
If a hosted music backend is revisited later, look for a provider offering an actual one-shot
clip API (worth checking Suno/ElevenLabs Music) rather than a live-streaming-only one.

## Verdict

- [x] Confirmed via real API calls, not assumption, that Lyria's one-shot request/response
  route doesn't work for audio despite the SDK's own type hints suggesting it should.
- [x] Confirmed the only working access path (live streaming session) is a genuine use-case
  mismatch against this project's job model, not just extra implementation effort.
- [x] No code changes -- `MusicGenerator`/`generation_registry` stay exactly as built for
  the music/sound-effect port extraction, ready for a real second adapter whenever one
  actually fits.
