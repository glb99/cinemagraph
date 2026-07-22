# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A command-line tool for generating lofi-style cinemagraphs (frozen background + one looping moving
element). Two independent input modes:

- **From a video clip** — auto-detects the moving region and produces a looping, graded cinemagraph.
- **From a single photo** — animates a still using built-in procedural motion effects (rain, snow,
  dust, ripple, sway, flicker, smoke), no source footage needed.

## Setup

```bash
pip install -r requirements.txt
```

No test suite, linter, or formatter is configured in this repo.

## Running

```bash
# From a video clip
python cli.py make input.mp4 output.mp4
python cli.py mask-preview input.mp4 mask_preview.png   # preview auto-detected mask before a full render
python cli.py make input.mp4 output.mp4 --mask mask_preview.png --no-grade

# From a single photo (no video needed)
python cli.py from-photo photo.jpg output.mp4 --effect smoke
python cli.py from-photo photo.jpg output.mp4 --effect rain --mask window_mask.png
```

Run `python cli.py make --help` / `python cli.py from-photo --help` for the full flag list (feather,
grade strength, grain, duration/fps/speed, GIF export, etc.) — flags are self-documenting via Click.

### Generating synthetic test inputs

There's no real sample footage checked in beyond `photo.jpg`. To exercise the video pipeline or try
photo effects without your own footage:

```bash
python examples/make_test_clip.py   # writes examples/test_input.mp4 (fake steam over a mug)
python cli.py make examples/test_input.mp4 examples/test_output.mp4 --gif

python examples/make_test_photo.py  # writes examples/test_photo.jpg
python cli.py from-photo examples/test_photo.jpg examples/test_output.mp4 --effect smoke
```

## Architecture

All logic lives in `cinemagraph/`, with `cli.py` as a thin Click wrapper that only parses flags and
calls into `cinemagraph.pipeline`. Two entry points, `make_cinemagraph` and
`make_cinemagraph_from_photo` in `pipeline.py`, each stitch together the same set of stages in
different order:

- **`io_utils.py`** — reads/writes video (`cv2.VideoCapture`/`VideoWriter`) and GIFs (`imageio`).
  Whole clips are read into memory as a `list[np.ndarray]` — fine for the few-second clips this tool
  targets, and keeps every other module operating on plain frame lists rather than streams.
- **`mask.py`** — produces a soft (feathered, float32 0..1) HxW mask marking which pixels stay
  animated. Either `auto_motion_mask` (frame-differencing + largest-connected-component + Gaussian
  blur) for video input, or `load_mask` for a hand-painted PNG. The photo pipeline uses the same
  `load_mask`, or no mask (animates the whole frame).
- **`loop.py`** — video-pipeline-only. `find_best_loop_point` trims a clip to where it naturally
  loops best (closest frame to frame 0 near the end); `crossfade_loop` blends the tail into the head
  so the loop point is invisible.
- **`photo_effects.py`** — procedural motion generators (rain/snow/dust share a particle engine;
  ripple/sway use `cv2.remap` pixel displacement; flicker modulates brightness; smoke layers sine
  noise fields). Every effect is a periodic function of `t = frame / n_frames`, so the sequence loops
  perfectly by construction — this is why the photo pipeline never calls `loop.py`. Motion rate is
  defined in `speed` (cycles/sec-equivalent), decoupled from `duration`, so lengthening a clip changes
  how often it loops rather than how fast the motion looks.
- **`grade.py`** — shared by both pipelines. `lofi_grade` applies lifted blacks, warm shift,
  desaturation, vignette, and grain to a single frame; `apply_grade_to_frames` maps it over a list.

### Pipeline order

**Video** (`make_cinemagraph`): read frames → auto-trim to best loop point → pick still frame →
build mask (auto or loaded) → composite still+live per-frame via the mask → crossfade loop →
grade → write.

**Photo** (`make_cinemagraph_from_photo`): read image → load mask (or none) → generate `n_frames`
via the chosen effect (already loops perfectly, no crossfade needed) → grade → write.

Both pipelines converge on `io_utils.write_video` / `write_gif` for output, and share `grade.py`
for the final color treatment.

Frames are `np.ndarray` in OpenCV's BGR uint8 format throughout; conversions to RGB only happen at
GIF-write time.
