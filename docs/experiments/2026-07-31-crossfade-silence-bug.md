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
