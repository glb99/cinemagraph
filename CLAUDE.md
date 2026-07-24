# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A command-line tool for generating lofi-style cinemagraphs (frozen background + one looping moving
element). Two independent input modes:

- **From a video clip** — auto-detects the moving region and produces a looping, graded cinemagraph.
- **From a single photo** — animates a still using built-in procedural motion effects (rain, snow,
  dust, ripple, sway, wind, flicker, smoke, vapor), no source footage needed. Effects are
  combinable (e.g. `--effect wind --effect vapor --effect dust`).

## Setup

Uses [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Installs the `cinemagraph` package (editable) plus the `api` extra and `dev` dependency group into
`.venv`. The heavy `ml` extra (torch/transformers, for the future semantic-mask sidecar) is declared
in `pyproject.toml` but deliberately **not** synced by default — only pull it in explicitly
(`uv sync --extra ml`) when working on that feature, since it doesn't belong in the core tool's venv.

Tests: `uv run pytest`. No linter/formatter configured yet.

## Running

Installed as a console script, `cinemagraph`, via `[project.scripts]` in `pyproject.toml`:

```bash
# From a video clip
uv run cinemagraph make input.mp4 output.mp4
uv run cinemagraph mask-preview input.mp4 mask_preview.png   # preview auto-detected mask before a full render
uv run cinemagraph make input.mp4 output.mp4 --mask mask_preview.png --no-grade

# From a single photo (no video needed)
uv run cinemagraph from-photo photo.jpg output.mp4 --effect smoke
uv run cinemagraph from-photo photo.jpg output.mp4 --effect rain --mask window_mask.png
```

Run `uv run cinemagraph make --help` / `uv run cinemagraph from-photo --help` for the full flag list
(feather, grade strength, grain, duration/fps/speed, GIF export, etc.) — flags are self-documenting
via Click.

### Generating synthetic test inputs

There's no real sample footage checked in beyond `photo.jpg`. To exercise the video pipeline or try
photo effects without your own footage:

```bash
uv run python examples/make_test_clip.py   # writes examples/test_input.mp4 (fake steam over a mug)
uv run cinemagraph make examples/test_input.mp4 examples/test_output.mp4 --gif

uv run python examples/make_test_photo.py  # writes examples/test_photo.jpg
uv run cinemagraph from-photo examples/test_photo.jpg examples/test_output.mp4 --effect smoke
```

## Architecture

Package lives at `src/cinemagraph/` (src-layout, to keep `import cinemagraph` from ever silently
resolving to the working copy instead of the installed package), with `cli.py` as a thin Click
wrapper that only parses flags and calls into `cinemagraph.pipeline`. Two entry points,
`make_cinemagraph` and `make_cinemagraph_from_photo` in `pipeline.py`, each stitch together the same
set of stages in different order:

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
- **`effects/`** — procedural motion generators, one plugin per module, registered against a single
  registry in `base.py` (`Effect(name, family, precompute, apply, defaults, allowed_kwargs)`,
  `EffectFamily.TONE` or `.PARTICLE`). `animate_photo()` (in `effects/__init__.py`) resolves the
  requested effect name(s) against the registry and chains them: rain/snow/dust (`particles.py`)
  share a particle engine and always composite last (`PARTICLE` family — they sit visually on top,
  regardless of the order effects were requested in); ripple/sway/wind (`ripple.py`/`sway.py`/
  `wind.py`) use `cv2.remap` pixel displacement (wind sums several sway-like octaves at different
  spatial/temporal rates for a turbulent, gusting look); flicker (`flicker.py`) modulates brightness;
  smoke/vapor (`clouds.py`) share a layered-sine-field "cloud" engine (vapor adds blur, lower
  opacity, and a deterministic rising carrier wave for a steam-like look) — these five are all
  `TONE` family, running in sequence on one shared frame before any `PARTICLE` effect is applied.
  Every effect is a periodic function of `t = frame / n_frames`, so the sequence loops perfectly by
  construction — this is why the photo pipeline never calls `loop.py`. Motion rate is defined in
  `speed` (cycles/sec-equivalent), decoupled from `duration`, so lengthening a clip changes how often
  it loops rather than how fast the motion looks. To add a new effect: write a `_precompute(ctx) ->
  dict` / `_apply(base, pc, t) -> ndarray` pair in a new module and `register()` an `Effect` for it
  (see any existing module, e.g. `ripple.py`, for the pattern) — nothing else needs to change.
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
