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
uv sync                    # core CLI only (opencv/numpy/click/imageio) + dev dependency group
uv sync --extra api        # + fastapi/uvicorn/httpx, for api/app.py
```

`dev` (pytest etc.) is a [PEP 735 dependency group](https://peps.python.org/pep-0735/), synced by
default and never part of a real install's metadata. `api` and `ml` are `[project.optional-dependencies]`
extras — real, installable features, opt-in via `--extra`. The heavy `ml` extra (torch/transformers,
for the future semantic-mask sidecar) is declared in `pyproject.toml` but deliberately **not** synced
by default, and shouldn't be added to the core tool's or the API's venv — only pull it in explicitly
(`uv sync --extra ml`) when working on that feature in isolation.

Tests: `uv run pytest` (needs `--extra api` synced first, for the API smoke tests). No linter/formatter
configured yet.

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
wrapper that only parses flags and calls into `cinemagraph.pipeline`.

`pipeline.py` is split into a **compute layer** (`render_video_cinemagraph`, `render_photo_cinemagraph`,
`render_mask_preview` — pure-ish, return frames/masks as data, never touch the output path) and a
**persist layer** (`save_cinemagraph_video`, `save_cinemagraph_from_photo`, `save_mask_preview` — thin
wrappers that call the matching `render_*` then write to disk). `cli.py` uses the `save_*` wrappers;
`api/app.py` uses the `render_*` functions directly since it wants control over exactly where output
bytes land (a job-scoped directory). Each `render_*` stitches together the same set of stages in
different order depending on video vs. photo input:

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

**Video** (`render_video_cinemagraph`): read frames → auto-trim to best loop point → pick still frame
→ build mask (auto or loaded) → composite still+live per-frame via the mask → crossfade loop →
grade. `save_cinemagraph_video` then writes the result (and optionally the mask preview PNG, GIF).

**Photo** (`render_photo_cinemagraph`): read image → load mask (or none) → generate `n_frames` via
the chosen effect(s) (already loops perfectly, no crossfade needed) → grade. `save_cinemagraph_from_photo`
then writes the result.

Both `save_*` wrappers converge on `io_utils.write_video` / `write_gif` for output; both `render_*`
functions share `grade.py` for the final color treatment.

Frames are `np.ndarray` in OpenCV's BGR uint8 format throughout; conversions to RGB only happen at
GIF-write time.

`validation.py` holds cross-entry-point request validation (currently: `resolve_effect_kwargs`,
checking that `from-photo`'s per-effect override flags belong to a requested effect and are real
overrides for it, per that effect's `allowed_kwargs`). It raises plain `ValueError` — a library
concern — and each entry point translates that into its own error type (`cli.py` → `click.UsageError`,
`api/app.py`'s effect-name check → HTTP 422).

## API layer

`api/` (sibling to `src/`, not part of the `cinemagraph` package — so the core library/CLI never
gain `fastapi`/`uvicorn`/`httpx` as hard dependencies):

- **`app.py`** — the FastAPI app and all routes. Kept thin like `cli.py`: routes only parse the
  request, delegate to `cinemagraph.pipeline`/`api.jobs`, and shape the response.
- **`jobs.py`** — in-process job tracking (`dict[str, Job]` in memory). No Celery/Redis — this is a
  single-process, local, no-auth personal tool; job state doesn't survive a restart, which is an
  accepted tradeoff, not an oversight.
- **`schemas.py`** — pydantic request/response models.

Renders (`POST /render/video`, `POST /render/photo`) accept a multipart file upload, save it into a
job-scoped directory under `CINEMAGRAPH_DATA_DIR` (default `./data`), and run the matching `render_*`
+ write via a `BackgroundTasks`-scheduled function — the endpoint returns a `job_id` immediately.
`GET /jobs/{job_id}` polls status (`pending`/`running`/`done`/`error`); `GET /jobs/{job_id}/file`
downloads the finished output.

`GET /capabilities` and `POST /mask/semantic` are the seam for the not-yet-built CLIPSeg service
(see `machine-learning/README.md`, directory named to match
[Immich](https://github.com/immich-app/immich/tree/main/machine-learning)'s convention for the same
core-app-plus-optional-ML-service split): both read `ML_SERVICE_URL` from the environment **at
request time**, never at startup, so the API always starts cleanly and simply reports the feature as
unavailable (`503` for `/mask/semantic`, `{"semantic_mask": false}` from `/capabilities`) when that
service isn't configured or isn't reachable — never a `500` or a failed startup.

Run locally: `uv run uvicorn api.app:app --reload` (needs `uv sync --extra api` first).
