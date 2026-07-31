# A silent gap between songs, with sound effects playing through it

**Date:** 2026-07-31
**Question:** the assembled audio only ever crossfades (overlaps) between consecutive songs.
The project owner wants the opposite option too -- an actual silent pause between songs --
but with sound effects continuing to play uninterrupted through that pause, not stopping too.

## Design

`gap_duration` added to `audio_track.build_music_track`, mutually exclusive with
`crossfade_duration` (a silent pause and an overlapping blend are opposite concepts, not two
ends of one dial -- `gap_duration > 0` wins if both are set). Implemented via ffmpeg's
`concat` audio filter with a synthetic `anullsrc` silent segment spliced between each pair of
real tracks -- `concat` alone only does hard joins with no gap of its own, so the silence has
to be a real input in the chain, not a parameter of `concat` itself.

The "sound effects keep playing through the gap" requirement needed no new code: it already
falls out of the existing pipeline shape (§5.6's own "independent tracks" decision) --
`layer_sound_effects` runs as a *separate* step after `build_music_track` produces the
finished music track (gap included), mixing effects continuously under whatever's there. The
gap only ever exists inside the music track; the effects layer doesn't know or care about it.

## A real verification pitfall, caught and worked around

First verification attempt used `ffmpeg -i out.wav -ss <t> -t 0.4 -af volumedetect -f null -`
on a small time window inside the expected gap -- and got a *non-silent* reading everywhere
across the whole file, including where a real gap should have been. Before concluding the
gap logic was broken, checked `anullsrc` in total isolation first (confirmed genuinely silent,
-91dB) and checked the exact same window via a completely different method: decoded the
concatenated WAV to raw PCM directly with `wave`/`numpy` and computed RMS per 0.5s window
myself, no ffmpeg filters involved. That showed the gap working exactly as intended (RMS=0.0
during the gap, real audio outside it) -- the `-ss`/`-t` + `volumedetect` combination was the
thing giving misleading readings for this particular check, not the feature. Recorded here so
a future verification pass doesn't repeat the same false alarm: for isolating a specific time
window's true silence/energy, decode to raw samples directly rather than trusting
`-ss`/`-t`-scoped `volumedetect` output.

## What was built

- `audio_track.build_music_track`: new `gap_duration: float = 0.0` param. `> 0` and more than
  one track: builds `[0:a][1:a][2:a]...concat=n=<N>:v=0:a=1[joined]` with a real `anullsrc`
  input spliced between each pair of songs; edge-fade total-duration math updated to *add*
  the gap time (instead of subtracting crossfade overlap, since a gap adds length rather than
  removing it).
- `pipeline.assemble`, `server/service.py`'s `run_assembly_job`, `server/app.py`'s
  `POST /assemble`, `cinemagraph assemble` (CLI `--music-gap`), and the web UI's Assemble tab
  (`Music gap (s)` field) all thread the new parameter straight through, no other logic
  changes needed anywhere in that chain.

## Verification

- `uv run pytest`: 116 passed (3 new -- gap inserts silence not crossfade with the right
  `concat`/`anullsrc` structure, gap extends (rather than shrinks) the edge-fade-out start
  time, gap is a no-op for a single track), 2 deselected.
- `scripts/golden_check.py`: unaffected.
- Real ffmpeg, raw-PCM-level verification (see the pitfall above): built a real 2-song music
  track with a 2.0s gap plus a real sound effect layered over it. Direct sample inspection
  confirmed RMS=0.0 during the gap in the music-only track, and non-zero, continuous RMS
  through that same window in the final music+effects mix (effect audible, music silent) --
  exactly the requested behavior.
- Confirmed live in the browser: the new "Music gap (s)" field is present on the rebuilt
  `core` container's Assemble tab.

## Verdict

- [x] `music_gap_duration` built, mutually exclusive with crossfade, threaded through the
  full CLI/API/UI stack with no changes needed to sound-effect layering.
- [x] Verified at the raw-sample level (not just duration/status checks) that the gap is
  real silence and sound effects genuinely continue through it.
- [x] A misleading verification technique was caught and corrected before being trusted,
  rather than either accepting a false "it's broken" conclusion or a false "it works"
  conclusion from an unreliable check.
