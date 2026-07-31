# Long-form assembly (§5.6): clips, music, sound effects into one video

**Date:** 2026-07-31
**Question:** §5.6 sat as the least-scoped roadmap item ("furthest out... ffmpeg-concat-level
tooling first; anything smarter is speculative") since it was written. The project owner wants
to build it for real: combine already-generated resources (cinemagraph clips, AI music, AI
sound effects) into one finished long-form video, with smooth crossfades between clips and
between songs.

## Scope, confirmed with the project owner before building (via AskUserQuestion)

- **Independent tracks, combined at the end** -- the video track (clips concatenated +
  crossfaded) and the audio track (music concatenated + crossfaded, sound effects layered in)
  are built independently, then muxed together as the final step. No forced 1:1 pairing between
  clip count and song count.
- **Sound effects are a continuous layer** under/over the music (`amix`), not positioned at
  specific timestamps.
- **The whole assembled audio also fades in/out at its very edges** (`afade`), not just
  crossfades between consecutive songs in the middle -- raised by the project owner mid-plan
  ("sometimes I will want ... turn down the volume" between songs) and confirmed this meant
  wanting edge fades in addition to the already-planned inter-song crossfades, not a
  replacement for them.
- **`assemble()` only combines already-rendered clips** -- no fresh rendering from photos in the
  same call.
- **Both asset IDs (API) and file paths (CLI)** are supported as input.

## Investigation before building: what does the codebase already have?

Exhaustive grep across `src/`, `scripts/` confirmed **nothing** -- no cross-clip video
concatenation, no video-to-video crossfade (the existing `loop.crossfade_loop` blends a single
clip's own tail into its own head for seamless looping, a different concept), no audio/video
muxing, no audio-to-audio crossfade, no audio-duration reading anywhere in core code. Genuinely
greenfield, matching what §5.6's own original text already said.

`imageio-ffmpeg` (already a core dependency) bundles a full, real ffmpeg binary
(`imageio_ffmpeg.get_ffmpeg_exe()`) -- capable of everything needed via `subprocess`, without a
new dependency. This is the first place in the project's rendering path that shells out to an
external process rather than calling numpy/opencv/imageio in-process -- a deliberate
architectural choice, not incidental (see `docs/DESIGN.md` §5.6's own writeup for the full
reasoning), flagged explicitly to the project owner during planning.

## Verifying the real ffmpeg filter syntax before writing any production code

Same discipline as every hosted-API integration this session: confirmed each filter against the
real, bundled ffmpeg binary with tiny synthetic fixtures before writing `src/assembly/`:

- `xfade` (video crossfade, needs an absolute `offset` into the filter chain -- computed from
  cumulative clip duration minus overlaps so far, since `xfade`, unlike `acrossfade`, doesn't
  self-align): confirmed with two `testsrc`/`testsrc2` clips.
- `acrossfade` (audio crossfade between songs, self-aligning, no offset needed): confirmed with
  two `sine` tone clips.
- `afade` (edge fade-in/out on the fully-joined track): confirmed chained after `acrossfade`.
- `amix` with `-stream_loop -1 -t <duration>` (looping a shorter sound effect to cover the full
  base track, then mixing): confirmed with a base + one looped effect.
- Final mux (`-map 0:v -map 1:a -c:v copy -c:a aac -shortest`): confirmed a 5s video + 8s audio
  correctly produces a 5s final file (audio trimmed to the shorter video).
- **Also discovered**: `imageio-ffmpeg` bundles ffmpeg only, not ffprobe. Duration probing
  instead parses the `Duration: HH:MM:SS.ss` line ffmpeg itself prints to stderr when given an
  input with no output requested (that invocation always exits non-zero -- expected here, not a
  failure, confirmed against both a real video and a real audio file).

## What was built

- `src/assembly/` (new top-level sibling package, same tier as `asset_library/` per §3.2's own
  scope test): `ffmpeg_runner.py` (`run_ffmpeg`/`probe_duration`, the only subprocess boundary),
  `video_track.py` (`build_video_track`, chained `xfade`), `audio_track.py`
  (`build_music_track` chained `acrossfade` + edge `afade`; `layer_sound_effects` via `amix`),
  `pipeline.py` (`assemble()`, ties the three together + final mux).
- Every function beyond `ffmpeg_runner.py` itself takes injected `run_ffmpeg`/`probe_duration`
  parameters (default: the real ones) -- the same DI convention `call_service`/`render_fn`/
  `music_generator` already use in `server/service.py`, so tests fake the subprocess boundary
  entirely rather than monkeypatching module internals.
- CLI: `cinemagraph assemble CLIP_PATHS... OUTPUT_PATH --music ... [--sound-effect ...]
  [--video-crossfade] [--music-crossfade] [--music-edge-fade]`, matching `make`/`from-photo`'s
  exact style.
- API: `POST /assemble` (`server/app.py`) + `run_assembly_job` (`server/service.py`) -- asset IDs
  in, resolved via `asset_library.get()` at the route (422 on any missing id, same
  eager-validation style `/render/photo` uses for unknown effects), background job (`def`, not
  `async def` -- no HTTP await, matching `run_render_job`'s own sync shape per `service.py`'s
  documented async-vs-sync rule), registers output as `kind="generated", tags=["assembled"]`.

## A real DI gap found and fixed during implementation

First draft had `build_video_track`/`build_music_track`/`layer_sound_effects` call
`ffmpeg_runner.probe_duration(...)` directly (module-level reference) rather than through an
injected parameter -- inconsistent with `run_ffmpeg`'s own injected-parameter treatment right
next to it, and with this project's own stated preference (`server/service.py`'s module
docstring) for explicit parameters over monkeypatching module attributes. Fixed by adding
`probe_duration=ffmpeg_runner.probe_duration` as an injected parameter everywhere `run_ffmpeg`
already was, before writing any tests against it -- caught by re-reading the DI convention while
writing the plan, not after tests revealed the gap.

## Verification

- `uv run pytest`: 109 passed (16 new: 14 in `tests/test_assembly.py` asserting filter-graph
  structure -- exact `xfade` offsets, `acrossfade` chaining, edge-fade start times, the "0
  disables edge fade" convention, single-clip/single-track copy-through shortcuts, `amix`
  input counts -- plus 2 in `tests/test_api_smoke.py`, one validating the unknown-asset-id 422,
  one a real end-to-end run through the actual route), 2 deselected (`tests/integration/`,
  unrelated). None of the unit tests touch real ffmpeg -- `_FakeRun`/a fake `probe_duration`
  fake the subprocess boundary per the DI convention above.
- `scripts/golden_check.py`: unaffected (unrelated code path).
- **Real, unmocked verification, twice** (this project's own established rigor):
  1. Synthetic-pattern filter-syntax checks (above), confirming each ffmpeg filter's exact
     syntax against the real bundled binary before any production code existed.
  2. The full real CLI command (`cinemagraph assemble`) against three *actual*
     `cinemagraph from-photo`-rendered clips (rain/snow/dust effects on the repo's own test
     photo) plus two synthetic tone tracks standing in for music (ACE-Step wasn't running; the
     assembly mechanics don't depend on the audio's actual content) and one sound effect.
     Confirmed: correct final duration (3 clips × 2s with 0.5s crossfades = 5.00s, matching
     `ffmpeg -i`'s own reported duration exactly), both video (h264) and audio (aac) streams
     present in the final mux, real non-blank frames at both a stable point and inside the
     crossfade window (pixel std ~38, not corrupted/blank).

## Verdict

- [x] `src/assembly/` built exactly to the confirmed scope -- independent video/audio tracks,
  edge fades added per the project owner's own follow-up question, sound effects as a
  continuous layer, asset-only inputs (no fresh rendering).
- [x] Real ffmpeg filter syntax (`xfade`/`acrossfade`/`afade`/`amix`) confirmed against the
  actual bundled binary before writing production code, not assumed from documentation.
- [x] CLI + API wired following this project's own established parity/DI/job-system
  conventions exactly -- no new infrastructure needed for job tracking or library registration.
- [x] A real DI inconsistency (unbound `probe_duration` calls) caught and fixed during
  implementation, before it could become a test-writing obstacle.
- [x] Verified twice against real ffmpeg -- synthetic filter-syntax checks, then the full real
  CLI command against real project-rendered clips.
