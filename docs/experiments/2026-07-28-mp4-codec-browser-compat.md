# Rendered video shows as "0-second" in the web UI

**Date:** 2026-07-28
**Question:** User reported the web UI's `<video>` preview shows a rendered cinemagraph as
0 seconds long / doesn't play, despite the render completing successfully and the file downloading
with a real, non-trivial size. Is this a rendering bug, or something else?

## What was tried

Reproduced directly rather than guessing: rendered a real photo through the CLI
(`cinemagraph from-photo`), then inspected the actual output file with the bundled ffmpeg binary
(`imageio_ffmpeg.get_ffmpeg_exe()`) instead of assuming from symptoms:

```
Stream #0:0[0x1](und): Video: mpeg4 (mp4v / 0x7634706D)
```

Confirmed the file itself is completely valid at the container level (correct duration, correct
frame count, correct fps) — the problem is specifically that `mp4v` is MPEG-4 Part 2, not H.264.
Traced the cause to `io_utils.write_video`: `cv2.VideoWriter_fourcc(*"mp4v")` was hardcoded for
`.mp4` output. Checked whether the obvious fix (`fourcc="avc1"`) would even work on this machine
before assuming it would:

```
Failed to load OpenH264 library: openh264-1.8.0-win64.dll
```

Confirmed: `cv2.VideoWriter` with `"avc1"`, `"H264"`, and `"x264"` all failed to open here; only
`"mp4v"` succeeded. This is a known, common limitation of prebuilt `opencv-python` wheels — H.264
encoding support depends on the OpenH264 codec DLL, which isn't bundled for patent/licensing
reasons, so `cv2.VideoWriter` silently falls back to MPEG-4 Part 2 instead of erroring loudly.

Checked whether the project already had a way around this rather than adding a new dependency:
`imageio-ffmpeg` (already a hard dependency, used elsewhere for GIF writing) bundles its own
portable ffmpeg binary. Confirmed it has real H.264 support built in:

```
--enable-libx264 --enable-libx265 ...
```

Rewrote `write_video`'s `.mp4` branch to use `imageio.get_writer(..., codec="libx264",
pixelformat="yuv420p")` instead of `cv2.VideoWriter`. Both of those are already the ffmpeg plugin's
own defaults (checked `FfmpegFormat.Writer._open`'s signature directly rather than assume) — passed
explicitly anyway since browser compatibility shouldn't quietly depend on an upstream default.
Also passed `macro_block_size=1` to prevent imageio from padding frame dimensions to a multiple of
16, which would have silently changed output dimensions from the input's.

## Result

Re-rendered the same photo: `ffmpeg -i` now reports `Video: h264 (High) (avc1 / 0x31637661),
yuv420p(progressive)` with correct duration/dimensions/fps. Ran the full test suite (65 tests,
including one new one — see below) — all pass, and `scripts/golden_check.py` (which diffs frames
in memory, before they ever reach `write_video`) is correctly unaffected.

Verified the actual user-facing symptom is fixed, not just the container-level metadata: submitted
a real render through the actual web UI form (photo tab, in-page `File`-via-`DataTransfer`
technique), waited for the job to finish, then read the resulting `<video>` element's own
`.duration`/`.videoWidth`/`.videoHeight`/`.readyState` properties directly — the exact properties
that would show "0:00" broken in the browser's native UI:

```json
{"duration": 1, "videoWidth": 480, "videoHeight": 320, "readyState": 4}
```

`readyState: 4` is `HAVE_ENOUGH_DATA` — fully loaded and playable, not just "metadata parsed."

Added `tests/test_pipeline_smoke.py::test_save_cinemagraph_video_uses_browser_compatible_codec` to
catch any regression back to a non-browser codec. Worth noting why the *existing* round-trip tests
(`test_save_cinemagraph_video_writes_readable_file`) never caught this bug in the first place:
they read the output back via `cv2.VideoCapture`, which can decode `mp4v` just fine even though
`cv2.VideoWriter` can't reliably encode H.264 on this machine — the tests were checking "OpenCV can
read what OpenCV wrote," which is blind to whether a browser could read it too. The new test checks
the actual fourcc tag instead. Sanity-checked it both directions: temporarily changing the codec
back to `"mpeg4"` fails it; the real fix passes.

## Verdict

- [x] Adopted — `write_video`'s `.mp4` path now goes through `imageio`'s ffmpeg plugin
      (`libx264`/`yuv420p`) instead of `cv2.VideoWriter`. Guarded by a codec-tag regression test.

## Notes

The new test accepts `{"avc1", "h264", "x264"}` as the fourcc, not just `"avc1"` — empirically, this
exact OpenCV build reports `"h264"` on read-back even though `ffprobe` reports `"avc1"` at the
container level for the same file. Different tools/versions report the same underlying codec
differently; don't assume a single exact string without checking.

## Follow-up: odd-dimension photos crashed the fix (same day)

Found via real usage through the Docker deployment, not the test suite: rendering a real
1920x1027 photo (odd height) failed with `[Errno 32] Broken pipe`, and the ffmpeg stderr showed the
actual cause: `height not divisible by 2 (1920x1027)`. `libx264`+`yuv420p` requires even width and
height (chroma subsampling halves each dimension). The `macro_block_size=1` passed above was meant
to preserve exact frame dimensions by disabling imageio's automatic padding-to-multiple-of-16 —
but that also disabled the padding that would otherwise round an odd dimension up to an even one,
so libx264 rejected the stream outright instead of imageio quietly fixing it up.

None of the existing test fixtures (or the synthetic ones added above) have an odd dimension, so
nothing caught this before it hit production usage.

Fixed by using `macro_block_size=2` instead of `1` — the true minimum needed for `yuv420p`
compatibility (rounds up by at most 1px only when a dimension is odd), rather than fully disabling
padding. Verified against the actual failing file (rebuilt the Docker image, re-rendered the same
1920x1027 photo): `imageio` logs `resizing from (1920, 1027) to (1920, 1028)` and the output plays
correctly (`h264`/`avc1`, correct 4s duration). Added
`test_write_video_handles_odd_dimensions` (a direct `io_utils.write_video` call with a synthetic
1027-tall frame) to `tests/test_pipeline_smoke.py` to guard against regressing back to
`macro_block_size=1`.

### Verdict (follow-up)

- [x] Adopted — `macro_block_size=2` instead of `1`. Guarded by a new odd-dimension regression test.
