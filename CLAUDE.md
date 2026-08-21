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
uv sync                       # core CLI only (opencv/numpy/click/imageio) + dev dependency group
uv sync --extra server        # + fastapi/uvicorn/httpx, for server/app.py
uv sync --extra server --extra generation   # + google-genai, for GeminiAdapter (sec 3.6)
```

`dev` (pytest etc.) is a [PEP 735 dependency group](https://peps.python.org/pep-0735/), synced by
default and never part of a real install's metadata. `server` and `generation` are the
`[project.optional-dependencies]` extras — real, installable features, opt-in via `--extra`.
`generation` is `src/generation/`'s own extra (sec 3.2's "light sibling module" tier: a thin
`google-genai` API client, not a multi-GB local model) — kept separate from `server` so a
deployment that only wants the self-hosted backends (SDXL/ACE-Step/Stable Audio) never pulls it
in. There used to also be an `ml` extra (torch/transformers) here, predating `machine-learning/`
becoming its own standalone project with its own `pyproject.toml`/deps; removed once confirmed
dead (nothing in `cinemagraph` or `server` ever imported torch/transformers, and the root
`Dockerfile` explicitly excluded it from every build).

Tests: `uv run pytest` (needs `--extra server --extra generation` synced first, for the API
smoke tests and the Gemini-adapter unit tests). No linter/formatter configured yet.

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

# Assemble already-rendered clips + music (+ sound effects) into one long-form video (sec 5.6)
uv run cinemagraph assemble clip1.mp4 clip2.mp4 out.mp4 --music song1.mp3 --music song2.mp3
```

Run `uv run cinemagraph make --help` / `uv run cinemagraph from-photo --help` / `uv run cinemagraph
assemble --help` for the full flag list (feather, grade strength, grain, duration/fps/speed, GIF
export, crossfade durations, etc.) — flags are self-documenting via Click.

### Generating synthetic test inputs

There's no real sample footage checked in beyond `photo.jpg`. To exercise the video pipeline or try
photo effects without your own footage:

```bash
uv run python examples/make_test_clip.py   # writes examples/test_input.mp4 (fake steam over a mug)
uv run cinemagraph make examples/test_input.mp4 examples/test_output.mp4 --gif

uv run python examples/make_test_photo.py  # writes examples/test_photo.jpg
uv run cinemagraph from-photo examples/test_photo.jpg examples/test_output.mp4 --effect smoke
```

## Task runner and preflight

`justfile` holds every common task -- `just` (not `make`, which isn't installed on the Windows host
this is developed on). `just` with no arguments lists the recipes. The ones worth knowing:

```bash
just doctor       # what's installed, running, reachable, and free right now
just api          # the API locally on :8000 (PORT=8010 to move it)
just ui           # Vite dev server on :5173, proxying to the API
just gpu-image    # SDXL + CLIPSeg in Docker; stops the audio satellites first
just gpu-audio    # ACE-Step + Stable Audio; stops image-generation first
just hosted       # every feature via Gemini/Lyria 3, no GPU at all
```

`gpu-image`/`gpu-audio` stopping each other's profile is the point of the file, not a convenience:
any two torch satellites resident at once exceed both this card's VRAM and Docker's VM RAM (see
`docs/experiments/2026-08-18-lazy-model-ttl-and-hf-cache-volumes.md`). That constraint was real but
unenforced before -- it lived in a comment and in whoever remembered it. It is the operator-level
version of the admission control surveyed in
`docs/experiments/2026-08-21-model-lifecycle-prior-art.md`.

`cinemagraph doctor` (`src/diagnostics/`) is the preflight: tooling (including the ffmpeg binary
bundled inside `imageio-ffmpeg`, which `which ffmpeg` would wrongly call missing), whether
`frontend/dist` was actually built and when, GPU free VRAM against SDXL's measured ~7147 MiB
footprint, which known ports are occupied, each satellite as configured-vs-reachable-vs-*wedged*
(the busy watchdog's 503), and whether the API answers. It starts nothing, changes nothing, never
prints the value of `GEMINI_API_KEY` (only whether it's set), and **exits 0 by default** -- being
down is what it's there to tell you about, so it must not fail a task chain. `--strict` inverts
that for a deploy gate or healthcheck.

Its own top-level package for the same reason `asset_library` is one: it's cross-cutting, and
`cinemagraph` must not learn about ACE-Step URLs or Docker. Stdlib only (`urllib`, `subprocess`,
`socket`, `nvidia-smi` parsed from CSV), because it has to work from a bare `uv sync` with no
extras -- which is exactly when you need it, since "the server extra isn't installed" is one of the
things it reports.

## Architecture

Package lives at `src/cinemagraph/` (src-layout, to keep `import cinemagraph` from ever silently
resolving to the working copy instead of the installed package), with `cli.py` as a thin Click
wrapper that only parses flags and calls into `cinemagraph.pipeline`.

`pipeline.py` is split into a **compute layer** (`render_video_cinemagraph`, `render_photo_cinemagraph`,
`render_mask_preview` — pure-ish, return frames/masks as data, never touch the output path) and a
**persist layer** (`save_cinemagraph_video`, `save_cinemagraph_from_photo`, `save_mask_preview` — thin
wrappers that call the matching `render_*` then write to disk). Both `cli.py` and `server/service.py`
use the `save_*` wrappers — the API just points them at a job-scoped directory. The `render_*`
functions' actual consumers today are `scripts/golden_check.py` and `tests/test_pipeline_smoke.py`,
which need frames rather than files. Each `render_*` stitches together the same set of stages in
different order depending on video vs. photo input:

- **`io_utils.py`** — reads video via `cv2.VideoCapture`; writes `.mp4` via `imageio`'s ffmpeg plugin
  (`libx264`/`yuv420p`), not `cv2.VideoWriter`, and writes GIFs via `imageio` too. `cv2.VideoWriter`'s
  H.264 encoding depends on an OpenH264 DLL most `opencv-python` wheels don't ship (patent/licensing
  reasons), so it silently falls back to `mp4v` (MPEG-4 Part 2) -- a valid, `cv2`-readable video that
  browsers categorically cannot decode for `<video>` playback, surfacing as a "0-second" unplayable
  clip in the web UI despite the file being perfectly valid otherwise. `imageio-ffmpeg`'s bundled
  binary has `libx264` built in regardless of what's available on the host's OpenCV install. Guarded
  by `tests/test_pipeline_smoke.py::test_save_cinemagraph_video_uses_browser_compatible_codec`, which
  checks the actual codec tag rather than just "did something readable get written" — the earlier
  round-trip tests never caught this because `cv2.VideoCapture` can read back what `cv2.VideoWriter`
  wrote just fine; the bug was encode-only. Whole clips are read into memory as a `list[np.ndarray]` —
  fine for the few-second clips this tool targets, and keeps every other module operating on plain
  frame lists rather than streams.
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

### Asset library

`src/asset_library/` — its own top-level package, separate from `cinemagraph`, because it's
explicitly cross-cutting: cataloging source/generated files from *any* service in this project
(cinemagraph renders, ACE-Step music, Stable Audio sound effects, CLIPSeg masks), not just the
photo/video pipeline. It started as `cinemagraph/library.py` but moved out once music/sound-effect
generation made it clear the library's job was never specific to cinemagraphs.

Content-addressed storage (SHA-256 hash = filename under `<root>/objects/`, so re-adding identical
bytes is a free no-op) with metadata in a small SQLite index (`<root>/index.sqlite3`, stdlib
`sqlite3`, no new dependency). Root defaults to `~/.cinemagraph/library`, overridable via
`CINEMAGRAPH_LIBRARY_DIR` — deliberately independent of `CINEMAGRAPH_DATA_DIR` (the API's ephemeral
per-job scratch space), since the library needs to work from the CLI alone. Zero dependencies beyond
the stdlib by design — see `docs/DESIGN.md` sec 5.1 for the placement rationale.

In `docker-compose.yml`, `CINEMAGRAPH_LIBRARY_DIR` is explicitly set to `/data/library` (a
subdirectory of the already host-mounted `/data`) -- without it, the default path resolves inside
the container's own throwaway filesystem (the container runs as root, so `~` is `/root`), which
survives a plain `restart` but is silently wiped by `docker compose down` + `up`, directly
contradicting this module's own "survives restarts" design intent. Found by testing the actual
sequence (add an asset, `down`, `up`, check `GET /library`), not assumed.

All four API generate/render routes (`/render/photo`, `/render/video`, `/generate/music`,
`/generate/sound-effect`) auto-register their output as a `kind="generated"` asset once the job
succeeds (`server/service.py`'s job functions each take an optional `library_kind`; `/mask-preview`
deliberately doesn't pass one, since a diagnostic mask preview isn't worth cataloging). Registration
is best-effort -- a library-side failure never flips a successful render to an error job. The CLI's
`make`/`from-photo` are deliberately **not** wired the same way: their output path is one the user
already chose and controls, unlike the API's easy-to-lose job-scoped directories, and
`cinemagraph library add <path>` already covers deliberate cataloging without auto-registering every
draft/iteration while tuning a mask or effect.

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
`server/app.py`'s effect-name check → HTTP 422).

## Server layer

`src/server/` (sibling *package* to `cinemagraph` under `src/`, in the same distribution, but not
part of the `cinemagraph` package itself — so the core library/CLI never gain
`fastapi`/`uvicorn`/`httpx` as hard dependencies). Named `server`, not `api`, matching
[Immich](https://github.com/immich-app/immich/tree/main/server)'s convention for this same role — the
old name overclaimed (this project is video + music + sound effects + masking, "api" describes only
the one protocol it happens to speak) and would have collided with the FastAPI instance's own name
(`server.app:app` reads cleanly; `app.app:app` would have been a stutter with a package literally
called `app`).

Also moved here from a top-level `api/` directory: setuptools'
`[tool.setuptools.packages.find] where = ["src"]` only ever discovered packages under `src/`, so a
top-level `api/` was never actually included in a real (non-editable) build — `pip install
cinemagraph-tool[server]` installed FastAPI and uvicorn but not this code. Only worked during
development because `pythonpath = ["."]` in pytest's config, and running uvicorn from the repo
root, both put the repo root on `sys.path` as a side effect. Fixed by moving the directory, not by
special-casing the packaging config further.

- **`app.py`** — the FastAPI app and all routes. Kept thin like `cli.py`: routes only parse the
  request, delegate to `cinemagraph.pipeline`/`server.jobs`/`server._external_service`, and shape the
  response.
- **`jobs.py`** — in-process job tracking (`dict[str, Job]` in memory). No Celery/Redis — this is a
  single-process, local, no-auth personal tool; job state doesn't survive a restart, which is an
  accepted tradeoff, not an oversight.
- **`schemas.py`** — pydantic request/response models.
- **web UI serving** (`app.py`'s `/`, `/ui/{path}`, `/assets/{path}`) — `GET /` serves
  `frontend/`'s built bundle (see the Frontend section below); `/ui/{path}` serves that same
  document so a client-side route survives a hard reload or a shared link; `/assets/{path}` serves
  the hashed bundles. The SPA fallback is scoped to `/ui` rather than mounted at the root because
  this API owns unprefixed top-level paths (`/library`, `/assemble`, `/jobs`) — a root-level
  fallback would shadow them. Files are served through ordinary routes rather than
  `app.mount(StaticFiles(...))`, since a mount resolves its directory at import time, which is
  exactly the "config frozen at import" problem `config.py` exists to avoid, and would make the
  path un-overridable in tests. Where the bundle lives is `CINEMAGRAPH_FRONTEND_DIR`
  (`Settings.frontend_dist_dir`, defaulting to this repo's `frontend/dist`); when it isn't there,
  the UI routes return a 503 page explaining how to build it and **every other route keeps
  working** — the same degrade-don't-fail rule the optional services follow. This replaced
  `server/ui.py`'s `INDEX_HTML` (a single self-contained page, inline CSS/JS, no build step),
  deleted 2026-08-14 once `frontend/` was a complete 1:1 port of it. That module existed as a
  plain Python string to sidestep the packaging trap the `api/`→`src/api/` move caught (non-`.py`
  files silently dropped from a real build); the bundle now dodges that a different way — it's a
  build artifact the Docker image builds in its own stage and copies in, never something
  `pip install` is expected to carry.
- **`config.py`** — `Settings(BaseSettings)` (from `pydantic-settings`) plus `get_settings()`
  (`@lru_cache`, injected into routes via `Annotated[Settings, Depends(get_settings)]`), replacing
  three inconsistent ways this package used to read `os.environ` (a module-level constant frozen
  at import — `DATA_DIR`, a helper re-reading on every call, and per-call lookups by magic string
  in `_external_service.py`). The `DATA_DIR`-at-import problem was concrete, not theoretical:
  `tests/test_api_smoke.py` had to `importlib.reload(server.app)` on every test just to change
  `CINEMAGRAPH_DATA_DIR`, which the `dependency_overrides[get_settings]` pattern (FastAPI's own
  documented approach) makes unnecessary — `dependency_overrides` wins over the cache. Also bundles
  each optional service's url/display-name/env-var-name into one `OptionalService` (`config.py`'s
  own `music_service`/`semantic_mask_service`/`sound_effect_service` properties), since those three
  facts were previously repeated together at every call site. Deliberately does **not** cover
  `CINEMAGRAPH_LIBRARY_DIR` — the `asset_library` package reads it directly, since that package is
  meant to stay usable standalone (no server extras installed) and importing `pydantic` there would
  break that. Two config mechanisms instead of one is the correct outcome given that tier discipline,
  not a half-finished migration.
- **`_external_service.py`** — shared client helper for calling *optional external services*:
  `service_available(env_var)` (boolean, for `/capabilities`) and `call_optional_service(env_var,
  method, path, ...)` (raises `HTTPException(503)` on missing/unreachable, safe to call from a
  background task too — `HTTPException` is a normal exception outside the request cycle, catch it
  and read `.detail`). This is the one place this pattern is allowed to repeat across services:
  sharing it doesn't cross the isolation boundary between the actually-separate services (each still
  owns its own process/deps/Dockerfile), because it's client-side code living entirely inside
  `server/`, the single codebase that calls all of them — see `docs/DESIGN.md` sec 3.3 for the full
  reasoning on why the services *themselves* must never share code with each other, only this layer
  may.

`server/app.py` holds **routes only** — parse, delegate to `server/service.py`, shape the response.
Every multi-step workflow lives in `service.py` (`run_render_job`, `run_photo_semantic_mask_job`,
`run_music_job`, `run_sound_effect_job`). That's the standard FastAPI router/service split, and it's
what makes those workflows unit-testable directly (`tests/test_api_service.py` covers ACE-Step's
failure and timeout branches, which were unreachable through an HTTP round-trip when this code lived
in `app.py`). Note for anything async added there: FastAPI runs a *sync* BackgroundTask in a
threadpool but an *async* one on the event loop, so a coroutine doing blocking render work must push
it off the loop with `asyncio.to_thread` — `run_photo_semantic_mask_job` does.

Renders (`POST /render/video`, `POST /render/photo`) accept a multipart file upload, save it into a
job-scoped directory under `CINEMAGRAPH_DATA_DIR` (default `./data`), and run the matching `save_*`
via a `BackgroundTasks`-scheduled function — the endpoint returns a `job_id` immediately.
`GET /jobs/{job_id}` polls status (`pending`/`running`/`done`/`error`); `GET /jobs/{job_id}/file`
downloads the finished output. `POST /render/photo` additionally accepts `mask_prompt`, which chains
segmentation into the render (see below); the generated mask is kept in the job directory, and the
job errors rather than falling back to an unmasked render if the ML service is unavailable.

Three more routes are optional-external-service seams, all using `_external_service.py`:

- **`POST /mask/semantic`** — proxies to `machine-learning/`, a small FastAPI service **this project
  owns and built** wrapping CLIPSeg (loaded via `transformers`, not the `timojl/clipseg` repo — that
  one isn't a real PyPI package) — see that directory's own README for its contract and how to run it.
  Directory named to match [Immich](https://github.com/immich-app/immich/tree/main/machine-learning)'s
  convention for the same core-app-plus-optional-ML-service split. Env var `ML_SERVICE_URL`. Response
  is proxied through as raw `image/png` bytes, same as the request came back from the service.
- **`POST /generate/music`** — proxies to a `MusicGenerator` adapter (`server/generation_ports.py`/
  `generation_adapters.py`, see §3.6), currently just `ACEStepAdapter` against an
  [ACE-Step](https://github.com/ace-step/ACE-Step) API server. Env var `ACESTEP_URL`. ACE-Step
  already ships its own FastAPI server and a published image (`ghcr.io/ace-step/ace-step-1.5:latest`,
  see `docker-compose.yml`'s `acestep` service, gated behind `--profile audio` like
  `sound-effects`/`image-generation`) — there is no wrapper for this project to write, only a client.
  That client is more involved than `/mask/semantic`'s single proxied call because ACE-Step's own API
  is itself an async job queue (`POST /release_task` → poll `POST /query_result` → `GET /v1/audio`):
  `ACEStepAdapter.generate()` drives that queue to completion inside *our* `BackgroundTasks` job
  (`server/service.py`'s `run_music_job`), which is why `POST /generate/music` needs no new
  status/download routes of its own — `GET /jobs/{job_id}` and `GET /jobs/{job_id}/file` already work
  for it unchanged. (Full request/response contract: ACE-Step's own `docs/api/API.md`, reachable via
  its `acestep-docs` skill.) `instrumental` (form field) is translated by `ACEStepAdapter` itself —
  not `run_music_job` — into ACE-Step's own instrumental marker (`"[Instrumental]"`) sent as the
  `lyrics` field, found by reading ACE-Step's own source (`acestep/api/server_utils.py`'s
  `is_instrumental`), not its REST docs, which don't mention a boolean `instrumental` field at all
  (that only exists on ACE-Step's separate OpenRouter-compatible wrapper). An empty `lyrics` field
  does *not* make the real server skip vocals on its own. `run_music_job`'s own provenance records the
  *lyrics actually submitted* by the caller, not this internal marker substitution — kept as the
  adapter's own concern precisely so that distinction holds.
- **`POST /generate/sound-effect`** — proxies to a `SoundEffectGenerator` adapter, currently just
  `StableAudioAdapter` against `sound-effects/`, a small FastAPI service **this project owns and
  built** (unlike ACE-Step) wrapping Stable Audio Open — see that directory's own README for its
  contract and how to run it. Env var `SOUND_EFFECTS_URL`. Unlike ACE-Step's job queue, that service's
  own `POST /generate` is a single synchronous call; `run_sound_effect_job` still runs it as a
  background job here purely because generation takes real time (tens of seconds to a couple
  minutes) and the HTTP connection shouldn't be held open for it. Validated end-to-end against a real
  GPU (both the service standalone and the full chain through this API) — see
  `docs/experiments/2026-07-27-audio-model-serving-research.md`.
- **`POST /generate/image`** — proxies to one of two registered `ImageGenerator` adapters
  (`server/generation_ports.py`/`generation_adapters.py`/`generation_registry.py`, see
  `docs/DESIGN.md` sec 3.6), chosen per request via an optional `model` field (default `sdxl`):
  - `sdxl` — `image-generation/`, a small FastAPI service **this project owns and built**
    wrapping Stable Diffusion XL (`diffusers`), same shape as `/generate/sound-effect` (env var
    `IMAGE_GENERATION_URL`, single synchronous `POST /generate` call, still run as a background
    job here for the same reason). Accepts an optional `reference_image` upload + `strength` for
    img2img (generate conditioned on a photo instead of pure text) — best-effort under
    heavy/rapid use on this project's 8GB GPU (real, accepted VRAM tradeoff, not a bug still
    being chased); see `docs/experiments/2026-07-30-image-to-image-generation.md`.
  - `gemini` — Google's hosted Gemini image API, called directly (no satellite container) via
    `generation/`, a light sibling module (`google-genai`, its own root-pyproject `generation`
    extra). Env var `GEMINI_API_KEY`; only registers (and only then appears in
    `GET /capabilities`'s `image_generation_models`) when that's set. A hosted API was tried
    first for image generation and fully reverted the first time (new Google AI Studio accounts
    require a non-refundable minimum prepay to use it at all) — see
    `docs/experiments/2026-07-29-image-generation-backend-choice.md` for that account, and
    `docs/experiments/2026-07-31-gemini-adapter.md` for adopting it a second time, this time
    coexisting with SDXL rather than replacing it. Supports img2img too (Gemini's own multimodal
    `input`); no `strength` equivalent (accepted in the call for interface parity, silently
    ignored).

  `GET /capabilities`'s own `image_generation` bool is `true` if *either* SDXL health-checks or
  Gemini is configured — the `/capabilities` under-reporting bug once open here (an
  `image-generation/`'s blocked event loop, unrelated to this port work) was root-caused and
  fixed earlier — see `docs/experiments/2026-07-30-image-to-image-generation.md`'s follow-up
  section.

All four routes read their env var **at request time**, never at startup, so the API always starts
cleanly and simply reports the feature as unavailable (`503` from the `POST` route, `false` from
`GET /capabilities`) when that service isn't configured or isn't reachable — never a `500` or a
failed startup.

**`POST /assemble`** (sec 5.6) is different from all four above: no optional external service, no
`service_available()` check — it combines already-generated library assets (clips, music, sound
effects) via `src/assembly/`, its own top-level package driving `ffmpeg` (bundled through
`imageio-ffmpeg`, already a dependency) directly via `subprocess` — the first place in this
project's rendering path that shells out to an external process rather than calling
numpy/opencv/imageio in-process, a deliberate choice (see `docs/DESIGN.md` sec 5.6 for the full
rationale). `clip_asset_ids`/`music_asset_ids` (required) and `sound_effect_asset_ids` (optional)
are resolved to file paths via `asset_library.get()` at the route (422 on any missing id). Video
clips crossfade (`xfade`), music tracks crossfade plus fade in/out at the whole track's edges
(`acrossfade` + `afade`), sound effects layer continuously under the music (`amix`), and the two
finished tracks mux together (`-shortest`). `run_assembly_job` (`server/service.py`) is a plain
`def`, not `async def` — no HTTP await, matching `run_render_job`'s own sync shape. Registers
output as `kind="generated", tags=["assembled"]`. See
`docs/experiments/2026-07-31-long-form-assembly.md`.

`POST /library`, `GET /library`, `GET /library/{id}`, `GET /library/{id}/file`,
`DELETE /library/{id}` are thin routes over the `asset_library` package (uploads are staged to a temp
file, hashed/copied into the library, then the temp file is discarded) — the exact same functions the
`cinemagraph library` CLI subcommands call.

Run locally: `uv run uvicorn server.app:app --reload` (needs `uv sync --extra server` first).

## Frontend

`frontend/` — the web UI, a React SPA that replaced `server/ui.py`'s single-page version
(behaviour-identical port, 2026-08-14; see `docs/experiments/2026-08-14-ui-port-to-frontend.md`).
Stack, all of it already pinned by the scaffold rather than chosen during the port: React 19,
Vite 6, TanStack Router (file-based routes) + TanStack Query 5, Tailwind v4 (`@tailwindcss/vite`),
shadcn conventions (`components.json`: new-york/neutral, `@/components/ui`, Radix `Slot` + CVA +
`tailwind-merge`), Biome, Playwright, bun.

```bash
cd frontend
bun install
bun run dev            # :5173, proxies API routes to VITE_API_PROXY_TARGET (default :8000)
bun run lint           # biome check --write --unsafe
bun run build          # vite build && tsc -b -- writes dist/, which the API serves at GET /
bun run test           # Playwright; needs a live backend (dev proxy, or a server serving dist/)
```

`build` runs Vite *before* `tsc` on purpose: the TanStack Router plugin generates
`src/routeTree.gen.ts` during the Vite build and that file is gitignored, so type-checking first
fails on any clean checkout — CI and Docker builds included.

- **`src/client/`** — generated from the live app's own OpenAPI schema by
  `scripts/generate-client.sh` (`@hey-api/openapi-ts`, axios, `throwOnError: true`), regenerated by
  a pre-commit hook whenever `src/server/` changes. Never edited by hand, and every request in the
  app goes through `DefaultService.*` so the client can't drift from the routes. The exceptions are
  media URLs (`/jobs/{id}/file`, `/library/{id}/file`), which are plain path strings in
  `lib/api.ts` because the browser fetches them itself as `src` attributes.
- **`src/routes/`** — one file per tab, all under **`/ui`** (`/` redirects there). The namespace is
  load-bearing: `server.app:app` serves its routes unprefixed, so a client-side `/library` or
  `/assemble` shadows a real endpoint — see `src/lib/tabs.ts`'s own comment and the decision log.
- **`src/hooks/useJobRunner.ts`** — the single submit-then-poll implementation shared by all six
  job-producing tabs, replacing the old single-page UI's `pollJob()`/`wireForm()`. Client-side
  validation failures
  go through the same `fail()` surface as job errors.
- **`src/components/`** — the shared pieces the tabs are built from: asset pickers with inline
  previews, project assign/filter selects, save-to-library, the config hints, the repaint waveform.
- **`tests/smoke.spec.ts`** — Playwright, against a live backend; covers the always-available tabs
  only, since the gated ones depend on which satellites happen to be running.

**How it gets served.** Two paths, and it matters which one you're looking at:

- **Developing the UI** — `bun run dev` on :5173, hot reload, API calls proxied to the backend.
  The `frontend` compose service is this, in a container.
- **Everything else** — the backend serves `frontend/dist` itself at `GET /` (see the web-UI-serving
  bullet under Server layer). The Docker image builds the bundle in a `frontend-build` stage and
  copies it to `/app/frontend/dist`, with `CINEMAGRAPH_FRONTEND_DIR` set to match; running from a
  source checkout, `bun run build` once is enough, since the default path is this repo's own
  `frontend/dist`.

So `:8000` serves whatever bundle was last built, and `:5173` serves what's on disk right now —
if a UI change doesn't show up at `:8000`, it's a stale `dist/`, not a caching bug. `GET /` sends
`cache-control: no-store` for exactly that reason (it names hash-suffixed bundles); the hashed
assets themselves are cached immutably.

## Design document and experiments log

`docs/DESIGN.md` is the living design document: project vision, the experimental-by-nature
architecture principles (capability tiers, seams-before-implementations, registries as the plugin
mechanism), the feature roadmap, researched-and-reasoned lab tooling choices, and a decision log for
every "why does X live here" call made so far — read it before making a structural decision that
might already be answered there. `docs/experiments/` is a lab notebook, one short file per experiment
(see `docs/experiments/TEMPLATE.md`) — write one especially when something *doesn't* pan out.
`scripts/golden_check.py` is separate from `tests/`: it catches rendered-pixel regressions that the
pytest suite deliberately doesn't check for (see that script's docstring).
