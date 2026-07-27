# Design Document — cinemagraph-tool

> Status: living document. Last substantial revision: 2026-07.
> Audience: the project's own future self. Written to survive long gaps between work sessions.

---

## 1. Vision

A personal tool for producing **sci-fi ambient music video visuals**: concept-art-styled
images brought to life with subtle, perfectly-looping motion effects (drifting mist, rain,
flickering light, swaying vegetation, rippling water), in the visual language of ambient /
sci-fi music channels on YouTube.

The end-to-end dream workflow:

```
idea / prompt ──> concept-art image ──> animated loop (cinemagraph) ──> long-form video
                       ▲                        ▲
                       │                        │
              reference library        effect + mask tuning
           (own images, videos,      (procedural today; ML-
            generated outputs)        assisted tomorrow)
```

Today the middle step (image → animated loop) is built and solid. Everything upstream
(generation, references) and some of downstream (compositing, audio) is future work.

## 2. The defining constraint: everything is experimental

This is not a product with known requirements. For nearly every planned capability we
**do not yet know the implementing technology**:

| Capability | Candidate technologies (all open) |
|---|---|
| Image generation | LLM-provider APIs (Gemini, etc.) · local diffusion models (`diffusers`) · none |
| Semantic masking | CLIPSeg · SegFormer · classical heuristics |
| Motion effects | current procedural (numpy/opencv) · optical-flow warping · AI video models |
| Reference storage | SQLite index · flat JSON · content-addressed store |
| UI | none (CLI/API only) · thin web client · something else |

**Therefore the architecture's first job is to survive continuous change.** A technology
choice must never leak past the module that made it. Rewriting the *inside* of a module
must always be cheap; rewiring *between* modules must almost never be necessary.

## 3. Architecture principles

These are distilled from patterns that worked in comparable open-source projects and from
this project's own history (see §7 Decision log).

### 3.1 A stable core, swappable satellites (ports & adapters, applied loosely)

The [ports-and-adapters / hexagonal](https://en.wikipedia.org/wiki/Hexagonal_architecture_(software))
idea, applied pragmatically rather than ceremonially: the core (`src/cinemagraph/`) knows
*nothing* about where inputs come from or which backend produced them. Every uncertain
technology sits behind a small, stable seam:

- `POST /segment` (HTTP) is the seam for semantic masking — the core neither knows nor
  cares whether CLIPSeg or something else answers it.
- `generate_image(prompt) -> bytes` will be the seam for image generation — Gemini today,
  a local model tomorrow, callers unchanged.
- `Effect(precompute, apply)` is the seam for motion effects — a new effect (or an
  ML-backed one) is one `register()` call.

When a technology is uncertain, **build the seam first, the cheapest real implementation
second, and the abstraction machinery never** (until a second implementation actually
exists).

### 3.2 Capability tiers decide *where* code lives

Two orthogonal questions place every new capability:

1. **Scope** — does the core need this to do its own job? (`cinemagraph` = "animate an
   existing image/video", nothing more.) If not, it is a sibling concern, never a
   submodule of the core.
2. **Weight** — how heavy are its dependencies? This decides the tier:

| Tier | Mechanism | Example |
|---|---|---|
| Core dependency | `[project.dependencies]` | opencv, numpy, click |
| Optional, same env | `[project.optional-dependencies]` extra | `api` (fastapi/uvicorn) |
| Light sibling module | own directory, root pyproject extra | `generation/` (planned, API-backend case) |
| Isolated service | own directory, **own pyproject/lockfile/venv/container** | `machine-learning/` (torch-class deps) |

This mirrors [uv's official guidance](https://docs.astral.sh/uv/concepts/projects/workspaces/):
extras for optional-but-same-environment features; fully separate projects (not workspace
members) for anything needing dependency isolation. We deliberately do **not** use a uv
workspace — a shared lockfile is exactly what the heavy/light split must avoid.

### 3.3 The registry is the plugin mechanism (the ComfyUI lesson)

[ComfyUI](https://howworks.ai/blog/how-comfyui-works)'s defining strength is that every
capability is a registered node behind one uniform interface, which let a giant ecosystem
grow without core rewrites. Our `effects/base.py` registry
(`Effect(name, family, precompute, apply, defaults, allowed_kwargs)` + `register()`) is
the same pattern at small scale.

Two conditions both have to hold before reaching for this, not just "more than one option
exists": the things being registered must be **genuine peers** (same call shape, same
runtime tier, same availability guarantees), *and* the registry must live inside whichever
module actually owns that capability -- never inside `cinemagraph` for something that
isn't conceptually part of animating an image (§3.2's scope test still applies on top of
this one).

- **Effects** qualify: all local, synchronous, always-available Python functions of equal
  weight. This is the one built so far.
- **A multi-backend `generation/`**, if it ever gets a second real backend worth swapping
  between, would qualify -- registered inside `generation/` itself, never in `cinemagraph`.
- **Mask sourcing does not currently qualify**, and isn't one -- deliberately. Hand-painted
  mask, auto-motion-detection, and a semantic-mask HTTP call aren't peers: the first two are
  local/synchronous/always-available, the third is a remote call that can be absent or time
  out. Today this is plain `if mask_path: ... else: ...` branching in `pipeline.py`
  (see `render_video_cinemagraph`/`render_photo_cinemagraph`), which is the right amount of
  structure for three fixed, structurally-different options -- a registry here would paper
  over a real difference (sync-and-guaranteed vs. async-and-optional) rather than simplify
  anything.

### 3.4 One engine, thin doors

All entry points (CLI, API, future UI) are dumb wiring over the same library calls —
no business logic in entry-point modules, ever (they're the hardest code to test).
`pipeline.py`'s compute/persist split (`render_*` returns data, `save_*` writes) exists
precisely so new doors need zero engine changes.

### 3.5 Invariants get tests; experiments don't (yet)

Tests protect what must *never* silently break, not what is still in flux:

- the mask contract (float32, HxW, 0..1),
- the loop-closure invariant (every effect periodic in `t`, whole-cycle counts),
- pipeline smoke (both paths produce decodable video),
- API surface smoke (routes exist, degrade correctly).

Experimental code gets tests **when it stabilizes**, not before. Chasing coverage on code
that may be deleted next week is waste.

## 4. Current state (implemented)

```
cinemagraph-tool/
├── src/cinemagraph/          # the stable core: pipeline, effects registry, mask, grade, loop, io, library
├── api/                      # FastAPI door: background jobs, uploads, library routes; extra `api`
├── machine-learning/         # reserved seam (README-only): CLIPSeg semantic masking
├── tests/                    # 44 tests: contracts, invariants, smoke (core + API + library)
├── scripts/golden_check.py   # pixel-regression check, separate from pytest (see sec 6)
├── docs/experiments/         # lab notebook, one file per experiment
├── Dockerfile + docker-compose.yml   # core image (never torch); ML service gated off
└── pyproject.toml            # uv-managed; extras: api, ml; dep-group: dev
```

Key properties already in place: 9 combinable procedural effects (tone/particle families,
perfect loops by construction), auto + hand-painted masking, lofi grade, arbitrary-length
looped output, CLI/API parity on the happy path (API lacks fine-tuning knobs — known gap),
graceful degradation when the ML service is absent (`/capabilities`, 503s), a persistent
content-addressed reference library (§5.1 — implemented; not yet wired into `make`/`from-photo`).

## 5. Feature roadmap (unordered — this is a lab, not a backlog)

### 5.1 Reference library (local storage for images / videos / generated outputs) — IMPLEMENTED

A persistent, queryable store of source material and outputs. Built per the design
direction below, informed by [Hydrus](https://hydrusnetwork.github.io/hydrus/faq.html)
(SQLite + hash-addressed files + tags-not-folders) and Immich (hashing for dedup):

- `src/cinemagraph/library.py` — **core-tier** module (both CLI and API need it; it's
  about the tool's own data, unlike the API's ephemeral jobs).
- Storage: files content-addressed by SHA-256 under `<root>/objects/` (free dedup —
  re-adding identical bytes is a no-op on disk, just a metadata touch), metadata in
  **SQLite via stdlib `sqlite3`** (`<root>/index.sqlite3`, zero new deps).
- Assets carry: hash (= id), kind (`reference`/`source`/`generated`), tags, provenance
  (arbitrary JSON — e.g. which prompt/effect produced a generated output).
- Root defaults to `~/.cinemagraph/library`, override via `CINEMAGRAPH_LIBRARY_DIR` —
  decided independent of `CINEMAGRAPH_DATA_DIR` (the API's ephemeral per-job scratch
  space) specifically so the library works from the CLI alone, no API involved.
- Surfaces: `cinemagraph library add|list|show|rm`, `POST/GET/DELETE /library[/...]`
  routes — both call the exact same `library.py` functions.
- **Not yet done**: `make`/`from-photo` still take plain paths, not library references;
  generated outputs don't yet auto-register. Natural follow-up, deliberately deferred
  rather than bundled into the initial build.

### 5.2 Image generation (backend genuinely undecided)

Per §3.1/3.2: new sibling `generation/` (NOT inside `src/cinemagraph/` — generating an
input is outside the core's conceptual boundary), one function
`generate_image(prompt) -> bytes`, first implementation = whichever external API is
cheapest to stand up. A local-model backend, if ever chosen, becomes an isolated service
(own pyproject, own container — same tier as `machine-learning/`, possibly a separate
sibling given diffusion's much larger resource profile than segmentation).

### 5.3 Semantic masking (`machine-learning/`, contract already reserved)

CLIPSeg behind `POST /segment`; the API's `/mask/semantic` proxy and capability flag
already exist. Building this = filling one directory + uncommenting two compose lines.
When it's built: load CLIPSeg via `transformers` (`CLIPSegProcessor`/
`CLIPSegForImageSegmentation`, checkpoint `CIDAS/clipseg-rd64-refined`), **not** via
`timojl/clipseg` directly — that repo isn't on PyPI (only `pip install git+https://...`,
an unversioned dependency on one person's repo staying reachable), whereas `transformers`
is a real, maintained PyPI package that happens to support CLIPSeg as one of many
architectures. Same distinction that matters for any future git-only research repo.

### 5.4 Effect realism improvements

Known gaps, no ML required: perspective-aware ripple (amplitude/wavelength scaling with
depth), true advection for smoke/vapor (translation, not in-place brightness modulation),
flow direction derived from mask shape. Pure `effects/` work; the registry means each is
an isolated change.

### 5.5 API/CLI parity, then a thin web UI

Close the known API gaps first (mask upload, per-effect overrides via the existing
`validation.py`, `/mask-preview` route, `--loop-duration`); the UI then needs nothing
the API doesn't already offer. UI stays a thin client per §3.4 — candidate stack decided
when we get there, not now.

### 5.6 Long-form assembly (furthest out)

Sequencing multiple loops into a full video (crossfades, timing, maybe audio-reactive
cuts). ffmpeg-concat-level tooling first; anything smarter is speculative.

### 5.7 Audio (music + sound effects) — both IMPLEMENTED

Three candidate models were researched (see decision log below for the full comparison);
they turned out to fall into genuinely different categories, not three instances of the
same remaining work:

- **Music — [ACE-Step](https://github.com/ace-step/ACE-Step) — done.** `POST
  /generate/music` in `api/app.py`, via `ACESTEP_URL`. ACE-Step ships its own FastAPI
  server and published image (`ghcr.io/ace-step/ace-step-1.5:latest`) — there was no
  wrapper to build, only a client. That client is non-trivial anyway, because ACE-Step's
  own API is itself an async job queue (`release_task` → poll `query_result` →
  `/v1/audio`); `_run_music_job` drives that queue inside our own `BackgroundTasks` job,
  reusing the existing `GET /jobs/{id}`/`GET /jobs/{id}/file` routes rather than adding
  new ones — the generic `Job` abstraction turned out to cover "poll a remote job queue"
  as well as "run a local render," with no changes needed to `jobs.py`. **Not yet
  validated against a live server** — no GPU/running instance was available to test
  against; only the "service absent" degradation path is proven.
- **Sound effects — Stable Audio Open, wrapped in `sound-effects/` — done, and
  validated for real.** The premature-to-build call from the first pass of this section
  was revisited and reversed: once sound-effect generation is meant to be a
  `cinemagraph-tool` feature (reachable via its own API, the way music now is), the
  alternative to a thin wrapper isn't "no server" — it's `api/app.py` shelling out to
  the script as a subprocess, which is exactly the fragile pattern the project's own
  `RESEARCH.md` warns against (`"parsing stdout, argument quoting, blocking"`). A real
  consumer existing changes the calculus `RESEARCH.md` was reasoning about. Generation
  logic ported verbatim from `audio-effect-generation/generate_rain_stableaudio.py`; the
  wrapper's only addition is loading the model once at startup instead of per-call.
  Validated end-to-end on a real GPU (RTX 4060, model already cached): the service
  standalone (`POST /generate` → valid 44.1kHz stereo WAV, correct duration) *and* the
  full chain through `cinemagraph-tool`'s own `POST /generate/sound-effect` → job
  polling → file download. Not validated: the Docker build itself (Docker wasn't running
  in the validating session) — Python-level logic is proven, containerization isn't.
- **CLIPSeg** — same "own the wrapper" category as Stable Audio Open, but *behind* it in
  validation maturity: nothing has confirmed CLIPSeg's segmentation quality is actually
  good enough for this project's images yet, whereas both audio models' output is now
  proven. Still not built.

Community Docker images exist for Stable Audio Open (e.g.
`ashleykleynhans/stable-audio-tools-docker`) but are deliberately not used — unlike
ACE-Step's maintainer-published image, these are unaudited third-party wrappers with no
accountability equivalent to a real package registry entry; not a trust level worth
extending to something that needs GPU access.

## 6. Laboratory tooling — what earns its place and what doesn't

Researched against solo-experimental reality, not team-production cargo culting.

### Adopt now / already adopted

| Tool | Why |
|---|---|
| **uv** (adopted) | instant env sync makes branch-hopping between experiments cheap |
| **pytest on invariants** (adopted) | the safety net that makes aggressive refactors safe — proven during phases 2–3 |
| **git branches as experiment isolation** | an experiment = a branch; merged if it survives, deleted if not. No tooling needed |
| **Capability flags via env vars** (adopted) | `ML_SERVICE_URL` unset = feature off. This *is* the feature-flag system this project needs |
| **`docs/experiments/` log** (adopted) | one short markdown note per experiment (what/why/verdict) — the lab notebook. Costs minutes, saves re-running dead ends |
| **Golden-frame checks** (adopted) | `scripts/golden_check.py`, diffs rendered frames against blessed PNGs in `tests/golden/`; catches "the refactor changed the pixels" instantly, offline. Kept separate from `tests/` on purpose — it's a regression check, not a correctness contract |

### Explicitly rejected (for now), with reasons

| Tool | Why not |
|---|---|
| **Feature-flag services** (LaunchDarkly etc.) | built for runtime toggling on live users; we have no users and no runtime — env vars + branches cover it |
| **Experiment trackers** (MLflow, W&B, MLXP) | built for hyperparameter sweeps producing metrics; our outputs are judged by eye. The experiments log + library provenance covers it |
| **Heavy CI/CD** | no deployment target exists. Adopt a *minimal* GitHub Actions workflow (`uv sync && uv run pytest`) only when the repo gets a remote — nothing more until releases exist |
| **DVC / data versioning** | no training data, no datasets. The reference library covers asset management |
| **Kubernetes / orchestration** | docker-compose is the ceiling for a single-machine personal tool |

Revisit triggers: a second contributor (→ CI becomes mandatory), a hosted deployment
(→ real flags/monitoring), any model training (→ experiment tracking).

## 7. Decision log

Decisions already made, with reasoning — so they aren't accidentally relitigated:

| Decision | Reasoning (short) |
|---|---|
| `src/` layout + uv + PEP 735 dep-groups | packaging bugs prevented; dev deps never leak into installs ([ref video](https://www.youtube.com/watch?v=mFyE9xgeKcA)) |
| Effects: 1 registry replaced 5 parallel dicts | single registration point; ComfyUI-style extensibility |
| `pipeline.py` compute/persist split | any new entry point gets rendering for free |
| `api/` outside the package, inside the repo, **no** own pyproject | depends on core but core never depends on it; same env/image/release always — a second package would be pure indirection |
| Heavy ML = separate project, not uv workspace | shared lockfile would reintroduce torch into the core ([uv docs](https://docs.astral.sh/uv/concepts/projects/workspaces/)) |
| `machine-learning/` naming (was `ml_sidecar/`) | matches [Immich](https://github.com/immich-app/immich/tree/main/machine-learning); "sidecar" wrongly implied same-pod k8s semantics |
| Degradation checked per-request, never at startup | core must start and work with every optional satellite absent |
| Jobs are in-memory and ephemeral | personal single-process tool; the *library* (§5.1), not jobs, is where persistence belongs |
| `version = "0"`, no semver | it's an application, not a published library |
| `library.py` is core-tier, not under `api/` | both CLI and API need the same storage/lookup logic; unlike jobs, it must survive restarts |
| `CINEMAGRAPH_LIBRARY_DIR` separate from `CINEMAGRAPH_DATA_DIR` | the library must work from the CLI alone; tying it to the API's job-scratch env var would make that impossible |
| Golden-frame check is a script, not a pytest test | it's a regression check against *previous* output, not a correctness contract against a spec — different kind of thing, see §3.5 |
| Vendoring ACE-Step's/any actively-developed upstream's source: rejected | copying code you don't maintain means owning its update churn forever; referencing its published image gets the same "one `docker compose up`" outcome for free |
| ACE-Step referenced by published image, never a local `build: context: ../ACE-Step-1.5` | a relative path to a sibling directory only works on one machine with one exact folder layout; a registry image reference is portable |
| Isolated services (`machine-learning/`, future audio wrapper) never share code with each other | that's exactly the coupling isolation exists to prevent; the actual shared surface (a `/health` route, "load model once") is a few lines — trivial duplication beats a shared dependency, same lesson as rejecting a uv workspace |
| `api/_external_service.py` — one shared client helper, used by every optional-service route | this *is* safe to share: it never crosses the isolation boundary, since it's client-side code living in the one codebase (`api/`) that already calls every one of these services |
| CLIPSeg must be loaded via `transformers`, never `pip install git+https://github.com/timojl/clipseg` | the original repo isn't a real PyPI package (git-install only); `transformers` is a maintained package that happens to support CLIPSeg as one of many architectures |
| Reversed the "premature" call on the Stable Audio Open wrapper and built it | `RESEARCH.md`'s "wait until reload cost hurts" reasoning was written for a standalone script with no caller; once it's meant to be a `cinemagraph-tool` feature, the alternative to a wrapper is subprocess-shelling from `api/app.py`, which the same doc calls fragile — a real consumer changes which of that doc's own criteria applies |
| `sound-effects/` service built and code-reviewed but not vendored from a third party | unlike CLIPSeg/ACE-Step, this one *is* code this project owns and wrote (ported from the proven `audio-effect-generation` experiment) — "own the wrapper" was always the plan for this capability, this just executed it |

### Placement quick-test for anything new

1. *Would this make sense if the API didn't exist?* No → `api/`. Yes → continue.
2. *Does the core need it to animate an image/video?* No → sibling directory. Yes → `src/cinemagraph/`.
3. *Torch-class dependencies?* Yes → own project + container. No → extra on the root pyproject.

## 8. Reference projects

| Project | What we take from it |
|---|---|
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | registry/plugin extensibility as the growth mechanism; reproducible pipelines as data |
| [Immich](https://github.com/immich-app/immich) | core + `machine-learning/` service split, naming, compose topology, hash-based asset handling |
| [InvokeAI](https://github.com/invoke-ai/InvokeAI) | the counter-model: polished fixed UI vs composable graph — we lean ComfyUI-ward (composability over polish) |
| [Hydrus](https://hydrusnetwork.github.io/hydrus/) | SQLite + content-addressed, tag-based local media library design |
| Hynek Schlawack's uv series | packaging/layout/entry-point discipline (already applied) |
