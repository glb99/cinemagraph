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
| Optional, same env | `[project.optional-dependencies]` extra | `server` (fastapi/uvicorn) |
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

Doors (CLI, API, future UI) parse input and delegate; they hold no multi-step workflows.
For the API that means `app.py` = routes, `server/service.py` = workflows — the standard
router/service split. (Note the original phrasing here invoked "entry-point modules are
the hardest code to test" — that rationale doesn't actually apply to `app.py`, which is
not a packaging entry point and is thoroughly TestClient-tested. The router/service split
is justified on its own merits, not by that borrowed argument.)

`pipeline.py`'s compute/persist split (`render_*` returns data, `save_*` writes) exists so
new doors need zero engine changes. Worth recording honestly: the predicted consumer of
`render_*` was the API, but the API writes to disk and uses `save_*`. The actual consumers
are `scripts/golden_check.py` and the pipeline tests. The split is still right; the
prediction about who'd need it was wrong.

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
├── src/
│   ├── cinemagraph/           # the stable core: pipeline, effects registry, mask, grade, loop, io, library
│   └── server/                # FastAPI door: app.py (routes) + service.py (workflows) +
│                               #   config.py (Settings/DI) + ui.py (GET / thin web UI: 4 tabs);
│                               #   sibling package to cinemagraph, same distribution, no own pyproject
├── machine-learning/          # isolated service: CLIPSeg semantic masking (validated)
├── tests/                     # 64 tests: contracts, invariants, smoke (core + API + library)
├── scripts/golden_check.py    # pixel-regression check, separate from pytest (see sec 6)
├── docs/experiments/          # lab notebook, one file per experiment
├── Dockerfile + docker-compose.yml   # core image (never torch); ML service gated off
└── pyproject.toml             # uv-managed; extra: server; dep-group: dev
```

This package was originally a top-level `api/` directory, then moved into `src/` (setuptools'
package discovery never actually included it in a real build otherwise), then renamed from
`api` to `server` (matching Immich's convention; "api" described only the one protocol it
happens to speak, not what the package actually does — orchestrate rendering, music, sound
effects, and masking behind whatever door reaches it). Both moves are in the decision log below.

Key properties already in place: 9 combinable procedural effects (tone/particle families,
perfect loops by construction), auto + hand-painted masking, lofi grade, arbitrary-length
looped output, full CLI/API parity (mask upload, per-effect overrides, `loop_duration`, a
`/mask-preview` route — see §5.5), graceful degradation when any optional service is absent
(`/capabilities`, 503s), a
persistent content-addressed reference library (§5.1 — implemented; not yet wired into
`make`/`from-photo`), and three optional external services behind that degradation seam:
`sound-effects/` (Stable Audio Open, validated end-to-end), `machine-learning/` (CLIPSeg,
validated end-to-end), and a client to ACE-Step's own server (music generation, only the
absent-service path proven).

## 5. Feature roadmap (unordered — this is a lab, not a backlog)

### 5.1 Reference library (local storage for images / videos / generated outputs) — IMPLEMENTED

A persistent, queryable store of source material and outputs. Built per the design
direction below, informed by [Hydrus](https://hydrusnetwork.github.io/hydrus/faq.html)
(SQLite + hash-addressed files + tags-not-folders) and Immich (hashing for dedup):

- `src/asset_library/` — its own **top-level package**, not part of `cinemagraph` (both
  CLI and API need it; it's about the tool's own data, unlike the API's ephemeral jobs;
  and its job is explicitly cross-cutting across every service in this project, not
  specific to cinemagraph renders — see the 2026-07-28 decision-log entry below for why
  it moved out of `cinemagraph/` after initially landing there).
- Storage: files content-addressed by SHA-256 under `<root>/objects/` (free dedup —
  re-adding identical bytes is a no-op on disk, just a metadata touch), metadata in
  **SQLite via stdlib `sqlite3`** (`<root>/index.sqlite3`, zero new deps).
- Assets carry: hash (= id), kind (`reference`/`source`/`generated`), tags, provenance
  (arbitrary JSON — e.g. which prompt/effect produced a generated output).
- Root defaults to `~/.cinemagraph/library`, override via `CINEMAGRAPH_LIBRARY_DIR` —
  decided independent of `CINEMAGRAPH_DATA_DIR` (the API's ephemeral per-job scratch
  space) specifically so the library works from the CLI alone, no API involved.
- Surfaces: `cinemagraph library add|list|show|rm`, `POST/GET/DELETE /library[/...]`
  routes — both call the exact same `asset_library` functions.
- **Auto-registration (2026-07-28)**: all four generate/render routes (`/render/photo`,
  `/render/video`, `/generate/music`, `/generate/sound-effect`) register their output as
  `kind="generated"` once the job succeeds, via an optional `library_kind` param on each
  `server/service.py` job function. `/mask-preview` deliberately doesn't pass one — a
  diagnostic mask preview isn't an asset worth cataloging. Registration is best-effort:
  a library-side failure never flips an already-successful job to an error, since the
  render/generate itself already produced a real, usable file. The CLI's `make`/
  `from-photo` deliberately stay unwired — see the decision-log entry below for why.

### 5.2 Image generation (backend genuinely undecided)

Per §3.1/3.2: new sibling `generation/` (NOT inside `src/cinemagraph/` — generating an
input is outside the core's conceptual boundary), one function
`generate_image(prompt) -> bytes`, first implementation = whichever external API is
cheapest to stand up. A local-model backend, if ever chosen, becomes an isolated service
(own pyproject, own container — same tier as `machine-learning/`, possibly a separate
sibling given diffusion's much larger resource profile than segmentation).

### 5.3 Semantic masking (`machine-learning/`) — IMPLEMENTED, validated

CLIPSeg behind `POST /segment`, loaded via `transformers` (`CLIPSegProcessor`/
`CLIPSegForImageSegmentation`, checkpoint `CIDAS/clipseg-rd64-refined`), **not** via
`timojl/clipseg` directly — that repo isn't on PyPI (only `pip install git+https://...`,
an unversioned dependency on one person's repo staying reachable), whereas `transformers`
is a real, maintained PyPI package that happens to support CLIPSeg as one of many
architectures. Same distinction that matters for any future git-only research repo.
`server/app.py`'s `/mask/semantic` proxy now returns the service's raw `image/png` bytes
unmodified (it previously called `.json()` on the response — a leftover from before this
service existed — fixed when this was built). Same shape as `sound-effects/`: own
`pyproject.toml`/`Dockerfile`, eager startup load, per-request inference. Validated
end-to-end against a real GPU (RTX 4060, reusing `audio-effect-generation`'s existing
`torch`/`transformers` install): standalone `POST /segment` on a real photo produced a
correctly-sized, non-degenerate grayscale PNG, and the full chain through
`cinemagraph-tool`'s own `POST /mask/semantic` (with `ML_SERVICE_URL` pointed at it)
round-tripped the identical bytes. See `machine-learning/README.md`'s Validated section
and `docs/experiments/2026-07-27-audio-model-serving-research.md`.

### 5.4 Effect realism improvements

Known gaps, no ML required: perspective-aware ripple (amplitude/wavelength scaling with
depth), true advection for smoke/vapor (translation, not in-place brightness modulation),
flow direction derived from mask shape. Pure `effects/` work; the registry means each is
an isolated change.

### 5.5 API/CLI parity — DONE; thin web UI — photo + video rendering

`POST /render/video` and `POST /render/photo` now accept everything `make`/`from-photo` do: mask
upload (in addition to the API-only `mask_prompt` semantic path), per-effect overrides validated
through the same `validation.resolve_effect_kwargs()` the CLI uses (`server/app.py`'s earlier
unused `validation` import was the tell that this was planned but not wired up — it's wired up
now), and `loop_duration`. A new `POST /mask-preview` route matches `cinemagraph mask-preview`.
Verified against a live server, not just TestClient: uploaded mask + `dust_count` override on
`/render/photo`, and a real `/mask-preview` job producing a downloadable PNG — see
`docs/experiments/`. Found and fixed in the process: `make`/`from-photo` let the
`loop_duration`+`also_gif` conflict raise a raw uncaught `ValueError` (a Python traceback) on the
CLI, unlike the effect-override validation which was already caught — now both routes and both
CLI commands translate it into their entry point's normal error shape (422 / `UsageError`).

`GET /` serves a single self-contained HTML page (`server/ui.py`'s `INDEX_HTML` — inline CSS/JS, no
build step, no framework), now with four tabs, one per capability the API exposes: **Photo** and
**Video** (both always shown — core capabilities, no configuration needed) and **Music** /
**Sound effects** (each hidden entirely, not just disabled, unless `GET /capabilities` reports the
matching flag true — the same `music_generation`/`sound_effect_generation` signal every other
optional-service consumer already uses; `mask_prompt` inside the Photo tab uses the identical
pattern for `semantic_mask`). All four forms share one `pollJob()`/`wireForm()` implementation,
since every `/render/*`/`/generate/*` route returns the same `{job_id}` shape and is checked via
the same `GET /jobs/{id}` contract — the only thing that differs per tab is which fields go into
the request and whether the result lands in a `<video>` or `<audio>` element.

Photo and Video are both built and verified against a live server (see `docs/experiments/`); Music
and Sound effects have forms wired to their routes but haven't been exercised against a live
ACE-Step/sound-effects instance from the UI specifically (the routes themselves have — see §5.7).
As with the first slice, per-effect override flags and video's grade fine-tuning knobs are left out
of every form — thin-client coverage of the common path, not full parity with every CLI/API field.

**Why a plain string in `ui.py`, not static files.** Non-`.py` assets need explicit
`package-data`/`MANIFEST.in` configuration to survive a real (non-editable) build — exactly the
class of bug the `api/`→`src/api/` move (§7 decision log) already caught once for this project.
A Python string constant sidesteps that risk entirely: it's ordinary source, packaged the same as
every other module, no separate config to get right or forget.

Verified beyond a TestClient smoke test: booted a real `uvicorn` server, loaded the page in an
actual browser, and drove the page's own JavaScript (not a bypass) — constructed an in-page
`File` object via `DataTransfer` (the standard technique for testing file inputs without a native
OS picker), checked an effect, submitted the real form, and confirmed the real polling loop
updated the DOM to `done` with a working `<video>` preview whose URL served real, downloadable
MP4 bytes. Also confirmed the client-side mask/mask_prompt mutual-exclusivity check (mirroring
the API's own 422 rule) fires correctly. See `docs/experiments/`.

Stack decision made here, worth recording: no framework, no build tooling, one HTML file — right
sized for "thin client wrapping an already-complete API" on a personal tool with no other
frontend precedent in the repo yet. Revisit only if the UI's own complexity outgrows a single
page (multiple views, client-side state worth managing) — not preemptively.

Parity is deliberately *not* symmetric in one direction: `POST /render/photo`'s
`mask_prompt` (segment-then-render in one call) has no CLI equivalent, because the CLI
lives inside the core package and can't reach an HTTP client without putting one there.
See the decision log.

### 5.6 Long-form assembly (furthest out)

Sequencing multiple loops into a full video (crossfades, timing, maybe audio-reactive
cuts). ffmpeg-concat-level tooling first; anything smarter is speculative.

### 5.7 Audio (music + sound effects) — both IMPLEMENTED

Three candidate models were researched (see decision log below for the full comparison);
they turned out to fall into genuinely different categories, not three instances of the
same remaining work:

- **Music — [ACE-Step](https://github.com/ace-step/ACE-Step) — done.** `POST
  /generate/music` in `server/app.py`, via `ACESTEP_URL`. ACE-Step ships its own FastAPI
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
  alternative to a thin wrapper isn't "no server" — it's `server/app.py` shelling out to
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
See §5.3 for the fourth capability in this same family: **semantic masking —
CLIPSeg, wrapped in `machine-learning/` — built and validated for real**, same "own the
wrapper" category as Stable Audio Open. Segmentation *quality* for this project's actual
images (as opposed to "does the service work at all") still hasn't been judged by eye
across a range of prompts/photos — that's a usage question, not a plumbing one.

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
| `server` (originally named `api`) is a sibling *package* to `cinemagraph` under `src/`, same distribution, **no** own pyproject | depends on core but core never depends on it; same env/image/release always — a second package would be pure indirection |
| Moved the package from a top-level `api/` directory into `src/api/` | `[tool.setuptools.packages.find] where = ["src"]` only discovers packages under `src/`, so a top-level `api/` was silently never included in a real build — confirmed by inspecting an actual `uv build` wheel: no `api/*.py` in it, `pip install cinemagraph-tool[api]` installed FastAPI/uvicorn but not this project's own API code. It only worked in dev because `pythonpath = ["."]` (since removed) and running uvicorn from the repo root both put the repo root on `sys.path` as a side effect, masking the bug. It still isn't part of the `cinemagraph` package or importable from it — it's a sibling under `src/`, not a subpackage — so the core library/CLI still never gain `fastapi`/`uvicorn`/`httpx` as hard dependencies. This is *not* a uv workspace: uv workspaces are for genuinely separate projects with their own lockfile/venv (see the workspace decision below); `cinemagraph` and this package are two packages of the *same* distribution, which is what `packages.find` with multiple top-level packages under `src/` is for |
| A uv **workspace** was considered and rejected for `cinemagraph` + this package, not just for the heavy services | the uv docs' own criterion for rejecting a workspace is conflicting requirements or wanting separate venvs per member — neither applies here (no dependency conflict, same `requires-python`, and both need to be co-installed, not isolated). A workspace would have been *appropriate* for this pair; it just wasn't *necessary*, since they're already one distribution. The rejection that matters is the heavy-ML-services one below, whose criterion (`requires-python` intersection, torch version conflicts, wanting separate venvs) genuinely applies there |
| Renamed `api` → `server` (directory, extra name, module paths) | matches [Immich](https://github.com/immich-app/immich/tree/main/server)'s convention for this exact role. "api" overclaimed: this package orchestrates rendering, music generation, sound-effect generation, and masking behind whatever door reaches it (HTTP today, conceivably something else later) — "api" describes only the protocol, not the job. It also collided with the FastAPI instance's own conventional name: `api.app:app` would have become `app.app:app` under the more obvious alternative rename ("app"), a stutter with a package literally named `app`; `server.app:app` doesn't have that problem. The `server` name change did *not* prompt renaming the distribution (`cinemagraph-tool`) or the `cinemagraph` package — those remain a separate, larger identity question, not resolved here |
| Heavy ML = separate project, not uv workspace | shared lockfile would reintroduce torch into the core ([uv docs](https://docs.astral.sh/uv/concepts/projects/workspaces/)) |
| Removed the root `ml` extra (`torch`/`transformers`) from `pyproject.toml` | predated `machine-learning/` becoming its own standalone project with its own deps; confirmed dead rather than assumed dead — nothing in `cinemagraph` or `server` ever imported those packages, and the root `Dockerfile` already excluded it from every build. Kept as a stray declaration it would have been a second, contradictory answer to "where do torch-class deps live" alongside the actual answer (an isolated service) |
| `machine-learning/` naming (was `ml_sidecar/`) | matches [Immich](https://github.com/immich-app/immich/tree/main/machine-learning); "sidecar" wrongly implied same-pod k8s semantics |
| Degradation checked per-request, never at startup | core must start and work with every optional satellite absent |
| Jobs are in-memory and ephemeral | personal single-process tool; the *library* (§5.1), not jobs, is where persistence belongs |
| `version = "0"`, no semver | it's an application, not a published library |
| `asset_library` is its own top-level package, not under `server/` (nor, since 2026-07-28, under `cinemagraph/`) | both CLI and API need the same storage/lookup logic; unlike jobs, it must survive restarts |
| `CINEMAGRAPH_LIBRARY_DIR` separate from `CINEMAGRAPH_DATA_DIR` | the library must work from the CLI alone; tying it to the API's job-scratch env var would make that impossible |
| Golden-frame check is a script, not a pytest test | it's a regression check against *previous* output, not a correctness contract against a spec — different kind of thing, see §3.5 |
| Vendoring ACE-Step's/any actively-developed upstream's source: rejected | copying code you don't maintain means owning its update churn forever; referencing its published image gets the same "one `docker compose up`" outcome for free |
| ACE-Step referenced by published image, never a local `build: context: ../ACE-Step-1.5` | a relative path to a sibling directory only works on one machine with one exact folder layout; a registry image reference is portable |
| Isolated services (`machine-learning/`, future audio wrapper) never share code with each other | that's exactly the coupling isolation exists to prevent; the actual shared surface (a `/health` route, "load model once") is a few lines — trivial duplication beats a shared dependency, same lesson as rejecting a uv workspace |
| `server/_external_service.py` — one shared client helper, used by every optional-service route | this *is* safe to share: it never crosses the isolation boundary, since it's client-side code living in the one codebase (`server/`) that already calls every one of these services |
| CLIPSeg must be loaded via `transformers`, never `pip install git+https://github.com/timojl/clipseg` | the original repo isn't a real PyPI package (git-install only); `transformers` is a maintained package that happens to support CLIPSeg as one of many architectures |
| Reversed the "premature" call on the Stable Audio Open wrapper and built it | `RESEARCH.md`'s "wait until reload cost hurts" reasoning was written for a standalone script with no caller; once it's meant to be a `cinemagraph-tool` feature, the alternative to a wrapper is subprocess-shelling from `server/app.py`, which the same doc calls fragile — a real consumer changes which of that doc's own criteria applies |
| `sound-effects/` service built and code-reviewed but not vendored from a third party | unlike CLIPSeg/ACE-Step, this one *is* code this project owns and wrote (ported from the proven `audio-effect-generation` experiment) — "own the wrapper" was always the plan for this capability, this just executed it |
| `machine-learning/` built following the exact `sound-effects/` shape (own pyproject/Dockerfile, eager startup load, raw-bytes response) | consistency across the two isolated services beats bespoke structure per service — a future third service should follow the same shape unless it has a genuine reason not to |
| `/mask/semantic`'s proxy fixed to return raw `image/png` bytes instead of `.json()` | it was written before `machine-learning/`'s actual contract (a PNG mask, per its README) was implemented against; caught and fixed while building the real service, not left as a silent mismatch |
| `server/service.py` split out of `app.py`; routes hold no workflows | the standard FastAPI router/service split ("routers should not do everything"). Concretely: `run_music_job`'s failure and timeout branches were unreachable through an HTTP round-trip and therefore untested — extracting them made 5 new unit tests possible. Adopted the router/service layer only; skipped `repositories/`/`models/`/DI-session layering from the same guides, which assumes a DB and team scale we don't have |
| A `background_tasks.add_task` job is plain `def` unless it has a genuine `await`-worthy operation (real network I/O); the moment it doesn't, it collapses to sync rather than `async def` + a manufactured `asyncio.to_thread` wrap | plain `def` gets Starlette's dispatch (`run_in_threadpool`) *unconditionally* — there is no way to write it wrong. An `async def` job with no real awaiting left is strictly worse: same runtime outcome, but now depends on the author remembering to wrap every blocking call correctly, with zero framework safety net if they forget (it compiles, it runs fine in casual testing, and it silently freezes the whole server the first time the omission is actually hit). `run_render_job` (no external calls) is sync; `run_music_job`/`run_sound_effect_job`/`run_photo_semantic_mask_job` (each makes a real HTTP call) are async. If a future job ever loses its last genuine `await` — e.g. a rewrite that makes a service call optional or removes it — it should collapse back to plain `def`, not keep an async signature that no longer describes anything real about the function |
| Prompt→mask chaining lives in `service.py`, not in the core library or the CLI | it's the one module allowed to know about *both* external services and the render pipeline; `cinemagraph/` still never learns HTTP exists, and `_external_service.py` still never learns renders exist |
| CLI deliberately has no `--mask-prompt` | `cli.py` lives inside `src/cinemagraph/`, so giving it service access would require putting an HTTP client in the core package. The CLI keeps `--mask <file>`; chaining is an API-side capability. Revisit only if the CLI moves out of the package (which would be the moment a third entry point appears) |
| `mask_prompt` errors the job rather than falling back to an unmasked render | silently animating the whole frame when segmentation is unavailable would produce something other than what was asked for — a wrong result is worse than a clear failure |
| Closed the API/CLI parity gap: mask upload, per-effect overrides, `loop_duration`, `/mask-preview` all added to the API | these were feature-complete on the CLI and simply never carried over when the API was built; `validation.resolve_effect_kwargs` already had no CLI dependency (raises plain `ValueError`, not `click.UsageError`), so wiring it into `server/app.py` was direct, not a rewrite |
| Fixed `loop_duration`+`also_gif` to raise a catchable error on the CLI, not just the API | found while adding the same check to the API routes: `make`/`from-photo` never caught `pipeline.py`'s `ValueError` for this specific conflict, so it surfaced as a raw traceback — an old bug, unrelated to the API work, fixed because building the API-side check made the gap obvious |
| `_LOOP_DURATION_GIF_ERROR`'s message reworded to name the field, not the flag | it's now surfaced through both a `click.UsageError` and an HTTP 422 `detail`; "`--gif`"/"`--loop-duration`" reads correctly in one and confusingly in the other, so the message names `also_gif`/`loop_duration` instead, which reads fine either way |
| First UI slice: one HTML file (`server/ui.py`'s `INDEX_HTML`), no framework, no build step, no static-file mount | right-sized for a thin client wrapping an already-complete API with no other frontend in the repo; a Python string sidesteps the exact packaging trap `api/`→`src/api/` already caught (non-`.py` assets silently dropped from a real build without explicit `package-data` config) |
| UI scope limited to photo rendering for this first slice | proving the pattern (feature-gate on `/capabilities`, drive the existing job-polling contract) matters more than covering every route at once; video/music/sound-effect UI are separate, equally-sized follow-ups, not a reason to delay shipping something usable |
| Adopted `pydantic-settings` + `Depends(get_settings)` for `server/`'s config, replacing three ad-hoc `os.environ` patterns | `DATA_DIR` was a module-level constant frozen at import — concretely forcing `tests/test_api_smoke.py` to `importlib.reload(server.app)` on every test just to change `CINEMAGRAPH_DATA_DIR`, which `dependency_overrides[get_settings]` (FastAPI's own documented pattern) eliminates outright, since it wins over the `@lru_cache`. `_external_service.py`'s per-call `os.environ.get(env_var)` lookups by magic string became one `Settings` instance with typed fields |
| `config.py` deliberately does not cover `CINEMAGRAPH_LIBRARY_DIR` | `asset_library` has zero deps beyond the stdlib by design; importing `server.config` there would smuggle a server-tier (`pydantic`) dependency into a package meant to stay usable standalone, the exact thing the tier discipline in §3.2 exists to prevent. Two config mechanisms (env-direct for the library, `Settings` for server) is correct here, not an inconsistency to "finish" |
| `OptionalService` bundles url/name/env-var per service | those three facts (e.g. ACE-Step's URL, "Music generation" for error text, `"ACESTEP_URL"` for the "not configured" message) were previously passed together at every one of `run_music_job`'s three `call_optional_service` call sites; one dataclass ends the repetition without adding a class hierarchy nothing else needs |
| Moved `library.py` out of `cinemagraph/` into its own top-level package `src/asset_library/` (2026-07-28) | the library's job was never specific to cinemagraphs — it's meant to catalog output from every service in this project (music, sound effects, masks, not just photo/video renders). Keeping it nested inside a package named after one specific feature was a naming mismatch that would only get more awkward as more services started depending on it. `CINEMAGRAPH_LIBRARY_DIR` and the `~/.cinemagraph/library` default path were kept as-is — those name the *product*'s data home, not the Python package, matching the sibling `CINEMAGRAPH_DATA_DIR` convention which is equally not tied to any one package name |
| `docker-compose.override.yml` for local dev (bind-mount `./src`, `uvicorn --reload`) | Compose auto-merges override files with no extra flags, so this is zero-friction — plain `docker compose up` gets hot-reload for free. Works because `uv sync` installs the project editable by default (confirmed via the venv's own `.pth` file); the image's install already resolves imports through `/app/src`, so a live bind mount over that exact path is sufficient. Verified for real: edited `server/ui.py` on the host while the container was running, confirmed the change appeared over `GET /` within seconds with no rebuild, then reverted and confirmed the reverse. Scoped to `core` only — `machine-learning`/`sound-effects` are edited far less often and carry slow-to-rebuild dependencies |
| `write_video`'s `.mp4` path switched from `cv2.VideoWriter` to `imageio`'s ffmpeg plugin (`libx264`/`yuv420p`) | `cv2.VideoWriter`'s H.264 encoding needs an OpenH264 DLL most `opencv-python` wheels don't ship; it silently fell back to `mp4v` (MPEG-4 Part 2) — a valid file `cv2.VideoCapture` reads back fine (why the existing round-trip tests never caught it), but browsers cannot decode for `<video>` at all, showing as a "0-second" clip in the web UI. Confirmed directly: `cv2.VideoWriter_fourcc(*"avc1"/"H264"/"x264")` all failed to open on this machine; only `"mp4v"` worked. `imageio-ffmpeg`'s bundled binary has `libx264` built in (`--enable-libx264` in its own reported build config) regardless of the host OpenCV's codec support, so this doesn't depend on what's installed on the machine running it. Verified against a real browser `<video>` element's `.duration`/`.videoWidth` properties (the exact properties that read as broken before), not just file existence |
| New regression test checks the actual codec tag, not just "file is readable" | `tests/test_pipeline_smoke.py::test_save_cinemagraph_video_uses_browser_compatible_codec` reads back the fourcc via `cv2.VideoCapture` and asserts it's one of `{"avc1", "h264", "x264"}` — accepting all three since different OpenCV backends/versions report the same H.264 codec differently (confirmed empirically: this exact build reports `"h264"` on read-back, not the `"avc1"` ffprobe shows at the container level). Sanity-checked both directions: temporarily reverting to `codec="mpeg4"` fails the test; the real fix passes it |
| `write_video`'s `.mp4` path uses `macro_block_size=2`, not `1` | `1` (chosen to preserve exact frame dimensions) also disables the padding libx264 needs for odd width/height, so a real 1920x1027 photo failed outright (`height not divisible by 2`) instead of the intended dimension-preserving behavior. `2` is the true minimum for `yuv420p` compatibility — pads by at most 1px only when a dimension is odd, instead of the default 16. Guarded by `test_write_video_handles_odd_dimensions` |
| The API's four generate/render routes auto-register output in the library (`kind="generated"`); the CLI's `make`/`from-photo` deliberately don't (2026-07-28) | the API's job output lives in an easy-to-lose, UUID-named per-job directory — auto-cataloging solves a real "where did that go" problem. The CLI's output path is one the user already chose and controls, so nothing's at risk of being forgotten, and `cinemagraph library add <path>` already covers deliberate cataloging; auto-registering every CLI run would instead fill the library with draft renders from iterating on a mask/effect, not something the user asked for |
| Library registration is best-effort, never allowed to flip a successful job to an error | the render/generate already succeeded and its file already exists by the time registration runs; a cataloging-side failure (e.g. the library's own storage location being unwritable) is a real but separate problem that shouldn't hide a working result from the caller. `service.py`'s `_register_in_library` swallows exceptions rather than letting them reach the job's own try/except |
| `run_music_job`/`run_sound_effect_job`/`run_photo_semantic_mask_job` take an explicit `call_service=call_optional_service` parameter instead of calling that name via module import; `run_photo_semantic_mask_job` additionally takes `render_fn=pipeline.save_cinemagraph_from_photo` the same way (2026-07-28) | mirrors `run_render_job`'s existing `render_fn` parameter — that function was already dependency-injected this way, so hardcoding these calls in the other functions was an inconsistency, not a deliberate choice. Tests previously reached these seams via `monkeypatch.setattr(service, "call_optional_service"/"service.pipeline", ...)`, which works but patches this module's namespace rather than using an explicit, visible-in-the-signature seam; passing a fake directly as `call_service=`/`render_fn=` is the same swap made explicit. Neither parameter ever varies across real callers (`app.py` never passes anything but the default for either) — the justification isn't production polymorphism, it's that an explicit parameter is a refactor-safe, self-documenting seam: `monkeypatch.setattr` has to guess the exact name a call site looks up, which silently stops faking anything if an import is ever restructured, while an injected parameter can't fail that way. Deliberately did **not** apply the same treatment to `asset_library.add` (called from the shared `_register_in_library` helper): that would mean threading a parameter through all four job functions to serve exactly one test, versus these two changes which each touch one function and mirror an already-established sibling |

### Placement quick-test for anything new

1. *Would this make sense if the API didn't exist?* No → `src/server/`. Yes → continue.
2. *Does the core need it to animate an image/video?* No → sibling directory (under `src/` if it's
   part of this distribution and needs installing, e.g. `src/server/`; a top-level directory if it's
   an independently-versioned project, e.g. `machine-learning/`). Yes → `src/cinemagraph/`.
3. *Torch-class dependencies?* Yes → own project + container. No → extra on the root pyproject.

## 8. Reference projects

| Project | What we take from it |
|---|---|
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | registry/plugin extensibility as the growth mechanism; reproducible pipelines as data |
| [Immich](https://github.com/immich-app/immich) | core + `machine-learning/` service split, naming, compose topology, hash-based asset handling |
| [InvokeAI](https://github.com/invoke-ai/InvokeAI) | the counter-model: polished fixed UI vs composable graph — we lean ComfyUI-ward (composability over polish) |
| [Hydrus](https://hydrusnetwork.github.io/hydrus/) | SQLite + content-addressed, tag-based local media library design |
| Hynek Schlawack's uv series | packaging/layout/entry-point discipline (already applied) |
