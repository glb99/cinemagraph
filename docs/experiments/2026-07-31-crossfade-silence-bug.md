# "The music crossfade doesn't work" -- root cause and two real ffmpeg bugs

**Date:** 2026-07-31
**Question:** right after landing `music_gap_duration`, the project owner reported the
regular crossfade between songs stopped working. Find out why.

## First hypothesis, ruled out: something broke in the gap-duration change

Re-read `build_music_track`'s default (`gap_duration=0`) code path -- unchanged from before.
Reproduced directly against real ffmpeg with synthetic constant-amplitude sine tones: correct
total duration (`4+4-1=7s` for a 1s crossfade), and a real, expected blend region at the join
point. Reproduced again against two *real* ACE-Step-generated MP3s pulled straight from the
library: also correct total duration (`30+30-2=58s`). The crossfade mechanism itself -- and
the parameter threading through `pipeline.assemble`/`run_assembly_job`/`POST /assemble`/the UI
-- was never broken. This ruled out a regression in the recent gap work.

## Second hypothesis, the real one: real generated songs fade to near-silence at their own tail

Decoded the real crossfaded output to raw PCM (`wave`/`numpy`, bypassing ffmpeg's own filters
entirely) and inspected RMS energy across the whole track. Two things stood out:

- Song 1's own last ~1 second measures RMS ~40 -- essentially digital silence -- against a
  ~1000-8000 range everywhere else in the same 30s track. A real, natural fade-out baked into
  the generated audio, not noise or a decode artifact.
- The crossfade region (the last 2s of song 1 overlapping the first 2s of song 2) showed a
  real, measurable dip in the combined output -- RMS dropping from ~7000 to ~700-1200 right at
  the join.

Conclusion: `acrossfade` was blending two *already near-silent* edges together, which is
audibly a dip/dropout, not a musical transition -- a real interaction between the crossfade
mechanism and the shape of AI-generated music (which commonly tapers to silence at its own
natural ending), not a bug in the mechanism itself.

## A real verification-methodology trap, hit and corrected mid-investigation

First attempt to measure "is this region silent" used `ffmpeg -i out.wav -ss <t> -t 0.4 -af
volumedetect -f null -`. Got inconsistent, non-silent readings *everywhere* in the file,
including regions that should have been genuinely silent by construction (confirmed separately
by encoding `anullsrc` alone and getting a correct -91dB reading). Rather than trust that and
conclude the whole feature was broken, cross-checked with a completely different method --
decoding to raw PCM directly and computing RMS myself -- which gave clean, sensible,
reproducible numbers. The `-ss`/`-t`-scoped `volumedetect` combination was the thing giving
misleading readings for this specific check, not the audio. (Also documented in
`2026-07-31-music-gap-between-songs.md`, which hit the same trap first.)

## Two real ffmpeg option bugs found while building the fix

Attempted a fix: trim each track's near-silent edges with `silenceremove` before crossfading.
First two attempts failed for reasons that turned out to be genuine ffmpeg-option
misunderstandings, found only by testing against the real binary:

1. **`silenceremove`'s `*_threshold` is a linear amplitude (0..1 for normalized samples), not
   decibels** -- despite `-25dB`-style values looking like they should work (some other ffmpeg
   filters, like `silencedetect`, do accept a `dB` suffix). Passing `-25dB` here didn't error;
   it silently produced an empty output file after the downstream `acrossfade` had nothing
   usable to work with. Fixed by using a real linear threshold (`0.02`, roughly -34dB) instead.
2. **`stop_periods` must be negative to trim from the end.** A *positive* value scans forward
   from the start of the audio and cuts at the Nth silence period it finds there -- which, for
   a song with any quiet passage mid-track, can cut way earlier than the actual trailing edge
   (one test run this way cut a 30s track down to ~15s of surviving audio per side, nowhere
   near just the true 1s silent tail). Fixed with `stop_periods=-1`.

Both were caught by actually running the real ffmpeg binary and checking real output
(duration, raw PCM), not by trusting the filter's parameter *names* to mean what they seemed
to mean.

## What was built

- `audio_track.build_music_track`: before chaining `acrossfade` for interior joins, each
  track gets its own `silenceremove` pass -- trailing-edge trim (`stop_periods=-1`) for every
  track except the last, leading-edge trim (`start_periods=1`) for every track except the
  first. The very first track's leading edge and the very last track's trailing edge are left
  untouched deliberately -- those are `edge_fade_duration`'s job (a controlled fade), not a
  crossfade join, and trimming them would fight the edge-fade's own timing.
- `acrossfade` itself also switched from the default linear curve (`tri`) to an equal-power
  curve (`curve1=curve2=qsin`) -- confirmed via testing that this alone wasn't the main cause
  (barely changed the dip), but it's a legitimate, low-cost improvement worth keeping (a linear
  crossfade curve is a well-documented source of a *center* loudness dip independent of content
  shape; equal-power avoids that specific artifact).
- `edge_fade_duration`'s own total-duration estimate now has an explicit comment noting it's
  approximate post-trim (durations are probed from the original files before trimming; the
  real output is very slightly shorter than the estimate) -- acceptable given trims are on the
  order of ~1s against tracks tens of seconds long, not worth a second probe pass for that
  precision.

## Honest residual limitation (reported, not hidden)

Trimming reliably removes the *worst* case -- true near-silence at a track's own edge. It does
not, and cannot, eliminate all loudness variation near a crossfade point: real music naturally
varies in energy throughout a track (the same test song ranged roughly 1000-8000 RMS across
its whole 30s length, quiet intro included), so if the crossfade point happens to land where
both songs are independently in a quieter (but not silent) passage, some residual dip is just
what that specific pair of songs sounds like overlapped there -- an inherent content
characteristic, not a bug further threshold-tuning alone can fully solve. Reported this
tradeoff to the project owner directly rather than presenting the trim as a complete fix.

## Verification

- `uv run pytest`: 117 passed (1 existing test updated for the new filter-graph shape, 1 new
  test asserting the exact per-track trim/no-trim structure -- first track trim-only-trailing,
  middle tracks both, last track trim-only-leading -- plus the acrossfade curve), 2 deselected.
- `scripts/golden_check.py`: unaffected.
- Real ffmpeg, raw-PCM verification against the actual real generated MP3s pulled from the
  library (not synthetic fixtures, for this specific check): confirmed the trim genuinely
  shortens the track by trimming the true silent tail (58.0s -> 57.0s, matching the ~1s silent
  region measured directly), and confirmed via the final code path (not scratch scripts) that
  the whole pipeline still builds successfully end to end.

## Verdict

- [x] Root cause found and confirmed via real generated audio decoded to raw PCM, not
  assumption or by-ear guessing.
- [x] Two real ffmpeg option bugs found and fixed during the investigation, each confirmed
  against the real binary before being trusted.
- [x] A misleading verification technique (`-ss`/`-t` + `volumedetect`) caught and worked
  around with a more reliable method, rather than either trusting a false "broken" or false
  "fixed" conclusion from it.
- [x] The fix's real, honest limitation (residual dip from natural musical dynamics, not
  fully eliminable) was reported directly rather than glossed over.

## Follow-up (same day): "the music stops abruptly always 2 seconds before finishing"

Reported immediately after the fix above shipped, with the extra detail "it worked well
before the gap feature addition" -- pointing at something introduced around that work, not
a pre-existing issue.

### Reproducing precisely

Rebuilt `build_music_track` in isolation with full defaults (`crossfade_duration=2.0`,
`edge_fade_duration=2.0`) against the same two real library music tracks used for the
crossfade-dip investigation, then decoded the result to raw PCM and scanned RMS across the
whole file, focused on the last several seconds specifically (not just the crossfade
midpoint this time). Found the last **~4-5 seconds** of the combined track were already
near-total silence (RMS ~15-25), well before a smooth 2-second fade-out would explain.

### Root cause: the *outer* edge was never trimmed, and it mattered more than expected

Decoded track 2 (the *last* song in the sequence) on its own, independent of any of this
project's code. Found it fades to genuine silence starting around 24s into its own 30s
length -- **6 seconds** of the song's own natural ending is already silent, not the ~1s seen
on the first bug's investigation. The trim fix from the crossfade-dip bug above deliberately
skipped the very last track's own trailing edge (and the very first track's leading edge),
reasoning "that's `edge_fade_duration`'s job, not a crossfade join." That reasoning held for
the crossfade-dip case, but broke here: with the outer edge never trimmed, that song's own
6-second natural fade-out passed straight through into the final output untouched, and
`edge_fade_duration`'s own fade -- computed from an *estimated* total duration that didn't
account for any trimming at all on the outer tracks -- ended up landing somewhere inside (or
near) that already-silent region instead of controlling real, audible content. The result:
several seconds of dead air well before the labeled end, which reads exactly like "the music
stopped abruptly," even though nothing was technically an off-by-one error -- the requested
2-second fade genuinely happened, just on top of audio that was already silent for several
seconds beforehand.

### The fix: trim every edge uniformly, and stop estimating durations entirely

Two changes, made together:

1. **Every track's leading and trailing edge gets trimmed now, including the very first
   track's start and the very last track's end** -- not just interior joins. This makes the
   "which edges get trimmed" logic uniform and easier to reason about, and means
   `edge_fade_duration` is now always working from a clean, silence-free boundary,
   regardless of what any specific source recording's own dynamics happen to be.
2. **Replaced the absolute-timestamp fade-out (`afade=t=out:st=<computed total duration -
   edge_fade_duration>`) with a duration-independent technique**: `[a]afade=t=in:st=0:
   d=X,areverse,afade=t=in:st=0:d=X,areverse[out]`. Reversing the stream turns "fade out the
   last X seconds" into "fade in the first X seconds of the reversed stream" -- a fade-in at
   `st=0` needs no knowledge of total duration at all, so reversing back afterward gives an
   exact fade-out at the real end, regardless of how much trimming happened upstream. This
   was necessary, not just tidier: with every track now trimmed by a variable,
   content-dependent amount (1 second for one song, 6 for another), any estimate of "total
   duration" computed from pre-trim probed durations was demonstrably unreliable -- exactly
   the kind of drift that caused this bug in the first place.

Confirmed via real ffmpeg (`areverse,afade=t=in,areverse` alone, before wiring it into the
real function) that this produces a clean, gradual fade ending exactly at the stream's real
final sample, with full volume unaffected everywhere before the fade window -- verified by
raw PCM inspection, not by ear.

### Verification

- `uv run pytest`: 115 passed (test suite restructured for the new uniform-trim + duration-
  independent-fade behavior -- every track's trim is now asserted the same way, the old
  "interior-only" trim test and the old absolute-fade-out-timestamp tests were replaced with
  ones matching the new filter-graph shape), 2 deselected.
- `scripts/golden_check.py`: unaffected.
- Real ffmpeg, raw-PCM verification against the same real problem songs: rebuilt with the
  fixed code and full defaults. Total duration correctly dropped to 51.4s (down from the
  buggy 57.0s -- now correctly reflecting that ~6s of the second song's own natural silence
  no longer counts as "real" audio time). RMS at the true end ramps smoothly from ~2400 down
  through ~430, ~42, to ~16 over the final ~1.5 seconds -- a real, gradual, controlled fade,
  not an abrupt dropout. RMS at the true start similarly ramps up smoothly from ~240 to
  ~1500 over the first 2 seconds.

### Verdict

- [x] Root cause found via the same rigor as the first bug -- real audio decoded to raw PCM,
  specific numbers measured, not guessed.
- [x] Fixed at the actual source of the fragility (an estimate that couldn't stay accurate
  once trimming became variable-and-content-dependent), not patched with a bigger fudge
  factor on the same estimate.
- [x] Verified the fix produces a real, smooth, gradual fade at both true edges of the
  output, landing exactly at the stream's actual boundaries -- not just "duration looks
  about right."

## Third round (same day): "still doesn't work well" -- default crossfade duration raised

Reported again after the second fix shipped, this time with a specific symptom via a
follow-up question: "volume dips or sounds weak during transitions" (not silence, not wrong
duration) -- through the real web UI Assemble tab, with real songs.

### Ruled out first: a stale deploy

Before investigating further, checked the actually-running `core` container's own loaded
code directly (`docker exec ... python -c "import inspect; ...build_music_track..."`) --
confirmed it had the latest fix (`areverse`, `silenceremove`, no `probe_duration` param
still present from before its removal). Not a caching/deploy issue; the dip being reported
is real, on the current code.

### Confirming the trim threshold itself is well-calibrated, not the cause

Measured the two real songs already used for the earlier investigations with `astats`: peak
levels -1.4dB and -3.7dB (close to 0dB -- normally mastered, not unusually quiet), RMS -16dB
to -20dB. In linear terms that's roughly 0.1-0.16, while the trim threshold (`0.02`, ~-34dB)
sits 15-18dB *below* the tracks' own average loudness -- correctly conservative, not
accidentally too lenient or too aggressive. Ruled out the threshold itself as the remaining
cause.

### What actually still causes a dip, and why crossfade duration matters

Even with true silence trimmed from every edge, real songs still vary *moderately* in
loudness near their own edges (a quieter musical passage, not silence) -- overlapping two
such regions during a *short* crossfade window means that quieter moment makes up a large
fraction of the whole transition, reading as a dip. Tested directly: built the same two real
songs with `crossfade_duration=2.0` (the then-current default) vs. `crossfade_duration=6.0`,
decoded both to raw PCM, and compared the RMS trough during the transition against the
surrounding level. At 2s, the trough was roughly 5-10x quieter than the surrounding audio
(matching the earlier investigation's finding). At 6s, the trough was only about 2-6x
quieter -- a real, measurable improvement, though not a complete elimination of the dip
(some residual dip remains inherent to overlapping two independently-dynamic songs at any
fixed point, as documented in the first round above).

### Fix: raised the default `crossfade_duration` from 2.0s to 5.0s

CLI (`--music-crossfade`), API/UI (`music_crossfade_duration`), and `build_music_track`'s own
default all raised together -- still fully overridable in either direction. Explicitly
documented as a practical mitigation, not a complete fix: a genuinely complete fix would mean
choosing *where* in each song to place the crossfade based on measured loudness (avoiding
each song's own quieter passages near its edges), not just extending a fixed-position window
-- real, scoped-out future work, not attempted here since it's a meaningfully bigger feature
than a default-value change.

### Verification

- `uv run pytest`: 115 passed (no test relied on the old default value -- every existing test
  passes `crossfade_duration` explicitly), 2 deselected.
- `scripts/golden_check.py`: unaffected.
- Confirmed the new default (`5.0`) is actually loaded in the rebuilt, redeployed `core`
  container via the same `docker exec` introspection technique used to rule out a stale
  deploy at the start of this round.

### Verdict

- [x] Ruled out a stale deployment before investigating further, rather than assuming the
  code fix from the previous round was insufficient without checking it was even running.
- [x] Confirmed via real signal measurement (not guesswork) that the trim threshold itself
  was correctly calibrated, narrowing the remaining cause to genuine content dynamics.
- [x] Measured a real, quantified improvement (2s vs. 6s crossfade dip depth) before
  committing to a default-value change, rather than picking a new default by feel.
- [ ] Full fix (loudness-aware crossfade positioning) explicitly scoped out as real future
  work, not silently promised or attempted speculatively.
