# Open-source readiness plan

**Status:** 2026-08-21. W0, W1, W2 shipped; W3–W5 proposed, not started. Decisions recorded at the end.
**Goal:** make this repo something a stranger can install, understand, run, and extend — without
reading the source to find out why a feature is missing.

Background research on how comparable projects (Immich, ComfyUI, Open WebUI, LocalAI, llama-swap)
handle install, config, and extension is in
`docs/experiments/2026-08-21-model-lifecycle-prior-art.md` and the discussion that produced this
plan.

## The read

The architecture is already open-source-ready. Satellites degrade cleanly, `/capabilities`
distinguishes "not configured" from "configured but down", the core has no torch, and the UI
already gates tabs on live capability. That work is done.

What isn't ready is **distribution** (nobody should build a multi-GB torch image on their laptop
to try a feature) and **diagnosis** (today, every misconfiguration is discoverable only by reading
comments in `docker-compose.yml`). Plus one hard blocker: there is no LICENSE, which makes the
repo legally unusable by anyone else regardless of how good the code is.

Six workstreams, ordered so each one is useful on its own if the next never happens.

---

## W0 — Licensing (blocking prerequisite) — ✅ done 2026-08-21

**Why first:** without a license file, default copyright applies and nobody may legally use, fork,
or contribute. Every other item is wasted effort until this exists. It is also the item most
likely to need a decision only the author can make.

**Changes**

- `LICENSE` at repo root. MIT or Apache-2.0 for a tool like this; Apache-2.0 additionally grants
  patent rights and is the safer default when ML dependencies are in play.
- `license` + `classifiers` in `pyproject.toml` (currently absent entirely).
- A **model licensing** section in the README. This is not boilerplate — it is a real constraint
  this project inherits and currently hides:
  - Stable Audio Open is a **gated** HF repo: the user must accept its license on the model page
    before any download works. This is the single most likely first-run failure.
  - SDXL ships under CreativeML OpenRAIL++-M, which carries use restrictions.
  - ACE-Step is a third-party image this project references but does not vendor.
  The project's own license does not cover model weights or their outputs, and the README must
  say so plainly rather than leaving a user to discover it after a 14GB download.

**Done when:** `LICENSE` exists, `pyproject.toml` declares it, and the README states both the code
license and the "model weights and outputs are governed by their own licenses" caveat.

**Outcome:** all three shipped. `LICENSE` is the canonical Apache-2.0 text fetched from
apache.org with the appendix filled in. `pyproject.toml` uses the PEP 639 form
(`license = "Apache-2.0"` + `license-files`), which required raising the build requirement from
`setuptools>=68` to `>=77` — the SPDX expression isn't understood before that, and pairing it with
a legacy `License ::` classifier is an error, so those classifiers are deliberately absent.
Verified by building: the wheel's metadata carries `License-Expression: Apache-2.0` and
`License-File: LICENSE`, with the file itself bundled at `dist-info/licenses/LICENSE`. The README
gained a Licensing section with a per-model table, the gated-repo warning for Stable Audio Open,
and the point that a permissive code license grants nothing about model outputs.

**Effort:** an hour, as estimated.

---

## W1 — `.env.example` and a README that leads with the easy path — ✅ done 2026-08-21

**Why here:** the cheapest possible fix for the largest number of "it doesn't work" reports, and a
prerequisite for W2 having anything to check against.

**Changes**

- **`.env.example`**, committed (`.env` itself stays gitignored). Every variable with a comment
  and, where relevant, a URL:
  - `HF_TOKEN` — required for `sound-effects`; link straight to the license-acceptance page.
  - `GEMINI_API_KEY` — optional; enables the `gemini` image and `lyria3` music backends.
  - `MODEL_TTL`, `BUSY_TIMEOUT` — model lifecycle, defaults documented.
  - `CINEMAGRAPH_DATA_DIR`, `CINEMAGRAPH_LIBRARY_DIR`, `CINEMAGRAPH_FRONTEND_DIR`.
  - The four satellite URLs, with a note that each is read per request, so an unset one degrades
    rather than failing startup.
- **README restructure.** Current order buries the best thing about this project. New order:
  1. What it is, with an example output.
  2. **The 30-second path**: `uv sync` → `cinemagraph from-photo`. No Docker, no GPU, no API keys.
     This works today and is a genuinely strong first impression.
  3. Docker: core only.
  4. Optional satellites, one profile at a time, each with what it costs (download size, VRAM).
  5. Everything else.
- **A scope-and-safety note in the first screen**, not buried: no authentication, jobs are
  in-process memory and don't survive a restart, and the service is not built to face the
  internet. These are deliberate tradeoffs already recorded in `DESIGN.md`; a stranger who puts
  this on a VPS needs to know before, not after.
- `CONTRIBUTING.md`: how to run the tests, the `docs/experiments/` convention, and the tier rules
  from `DESIGN.md` §3.2/§3.3 that a drive-by PR would otherwise violate.

**Done when:** a new user can go from clone to a rendered cinemagraph without opening any file
other than the README, and every env var in `docker-compose.yml` appears in `.env.example`.

**Effort:** half a day, mostly writing.

---

## W2 — `cinemagraph doctor` — ✅ done 2026-08-21

**Why here:** turns every silent misconfiguration into a message with a fix. It pays for itself in
support burden immediately, serves CLI and Docker users equally, and is genuinely useful for our
own debugging — the satellite work in `fix/satellite-busy-watchdog` would have been easier to
verify with it.

**Design constraint, decided up front:** `doctor` lives in the `cinemagraph` core tier, which must
not gain `httpx` or `pydantic` (§3.2). So it reads `os.environ` directly (exactly as
`asset_library` already does) and uses stdlib `urllib.request` for reachability. It must not
import `server.config`. Getting this wrong is the obvious way this workstream quietly breaks the
tier discipline.

**Changes**

- New `src/cinemagraph/doctor.py`: each check a small pure function returning a result record
  (name, status, detail, fix). The pure-function shape is what makes it testable and is the
  reason this isn't just inlined into `cli.py`.
- `cli.py` gains a thin `doctor` subcommand — parse and delegate only, per §3.4.
- Checks, each with a concrete fix line:
  - ffmpeg resolvable via `imageio-ffmpeg`'s bundled binary.
  - `CINEMAGRAPH_DATA_DIR` and `CINEMAGRAPH_LIBRARY_DIR` exist and are writable.
  - Library index openable; asset count.
  - `.env` present when running under Docker.
  - Each of the four satellites: configured? reachable? and — new since the watchdog work —
    report `/health`'s `model_loaded` and `inflight_seconds`, so "stuck" is visible from the CLI.
  - `GEMINI_API_KEY` present (presence only; never print or validate the key remotely).
  - GPU: `nvidia-smi` present and what it reports. Informational, never a failure — this tool is
    fully usable on CPU.
- Exit non-zero if any check fails, so it can be used in CI or a container healthcheck.
- `tests/test_doctor.py`: the check functions are pure, so they test directly. Covers a passing
  environment, a missing directory, and an unreachable satellite.

**Done when:** `uv run cinemagraph doctor` prints a status table on a clean checkout, and each
failing line names the command or URL that fixes it.

**Outcome:** shipped as `src/cinemagraph/doctor.py` (checks return `CheckResult` records, never
print) plus a thin `doctor` command in `cli.py`. The tier constraint held and is now verified
rather than asserted: importing the CLI pulls in no `httpx`, `pydantic`, `fastapi`, `server`, or
`torch`.

Four statuses rather than pass/fail, because "not configured" and "configured but broken" are
genuinely different: `ok`, `off` (an optional feature nobody enabled — the expected state, and
flagging it would train people to skim), `warn` (configured but unreachable, or wedged), `fail`
(something core is actually broken). Exit code is non-zero only on `fail`, so an optional service
being down doesn't break a deploy gate.

It also reads the satellites' new `/health` fields, so a wedged model shows up as
`reports a wedged request (1500s in flight)` rather than as a generic outage — the CLI and the web
UI will report the same state from the same source.

25 tests in `tests/test_doctor.py`, none needing a live stack. One behaviour worth noting: the
library check deliberately does *not* call `asset_library.library_root()`, because that function
creates the directory as a side effect and a diagnostic must not change what it diagnoses — there's
a test asserting the directory stays absent.

**Effort:** as estimated.

---

## W3 — Published images and a release compose file

**Why here:** this is what makes "one command" honest. Today `docker compose --profile image up`
*builds* SDXL's torch image locally. Immich's users pull; ours compile. This is the highest-value
item for adoption and also the riskiest to execute, which is why it sits after the two cheap wins.

**Changes**

- New `.github/workflows/publish-images.yml`, following this repo's existing CI conventions
  (SHA-pinned actions, minimal `permissions`, zizmor-clean):
  - Triggers on `v*` tags, not every push — these images are large.
  - Publishes `core` plus the three satellites we own to `ghcr.io/<owner>/cinemagraph-*`.
  - `linux/amd64` only. CUDA-capable multi-arch is not worth the build time here.
- New **`docker-compose.release.yml`**: `image:` references only, no `build:` keys, attached to
  each GitHub release. `docker-compose.yml` stays the development file that builds from source.
  This mirrors Immich, which tells users to fetch the compose file from the *release* rather than
  from `main` precisely so a mid-refactor main branch can't break new installs.
- README install section points at the release file with a pinned version tag.

**Risks, called out because they are likely rather than hypothetical**

- **GH Actions runners may not have the disk to build torch images.** Standard runners ship
  ~14GB free; a CUDA torch image can exceed that. Mitigations, in order: build satellites on tags
  only, use a free-disk-space step, use registry-backed layer caching. If it still doesn't fit,
  the fallback is publishing `core` from CI and the satellites from a local `docker buildx` — less
  tidy, still a large improvement over every user building their own.
- **Verify compose's exact behaviour when `image:` and `build:` are both present** before relying
  on it. The intended UX is pull-by-default with `--build` as the opt-in; the separate release
  file sidesteps the ambiguity entirely, which is why it's the recommendation.
- Publishing images means publishing a supply chain. Keep the existing pinned-SHA discipline, and
  consider build provenance attestation.

**Done when:** `docker compose -f docker-compose.release.yml --profile image up` starts SDXL on a
machine that has never cloned the repo.

**Effort:** two to three days, dominated by CI iteration.

---

## W4 — A Setup tab in the web UI

**Why here:** small, and mostly a new *view* over data that already exists. Deliberately after W3,
because the most useful thing it can tell a user is "run this command", and that command should be
the pull-based one.

`/capabilities` already returns availability plus `configured`, and `frontend/src/lib/tabs.ts`
already carries a per-capability `startCommand`. The gating UX is built; what's missing is a place
that shows *everything* at once rather than one hint inside whichever tab you happened to open.

**Changes**

- `/capabilities` gains two fields: the app `version`, and each optional service's env var name
  (`OptionalService` already holds `env_var`, so this is plumbing, not new state).
- New `frontend/src/routes/ui/setup.tsx`, added to `TABS` as `always: true`.
- One card per capability: state (available / configured but not running / not configured), the
  env var that configures it, and the copy-pasteable command that starts it. Where the satellite
  is up, show `model_loaded` and `inflight_seconds` from its `/health` — the same signal `doctor`
  reports, so the CLI and the UI agree.
- Regenerate the API client through the existing pre-commit hook; never hand-edit `src/client/`.
- Extend `tests/smoke.spec.ts` — Setup is always-available, so it belongs in the existing
  always-available coverage.

**Done when:** the Setup tab explains the state of every optional capability without the user
opening a terminal or a source file.

**Effort:** one to two days.

---

## W5 — Effects as installable plugins (entry points)

**Why last, and conditionally:** this is the one item with no current demand. `DESIGN.md` §3.1 is
explicit — build the seam first, the abstraction machinery never, until a second implementation
exists. The seam (`effects/base.py`'s registry) is already built and already sufficient for
in-repo effects. **Recommendation: gate this on someone actually wanting to ship an effect
out-of-tree.** It is planned here so the design is settled when that day comes, not so it gets
built next week.

When it is built, entry points are the right mechanism: they are stdlib
(`importlib.metadata`), they inherit pip's trust and versioning model, and they require no
registry, no marketplace, and no remote-code-execution surface of our own. ComfyUI's custom-node
ecosystem is the cautionary counter-example — it needed security levels, an
`allow_git_url_install` flag, and a ban mechanism, because installing a node means running
arbitrary code from an arbitrary git URL.

**Changes**

- `effects/__init__.py` gains `_load_plugins()`, reading
  `entry_points(group="cinemagraph.effects")` after the built-in imports.
- Contract: an entry point resolves to a **callable returning an `Effect`**, not a module that
  registers as an import side effect. Explicit beats implicit, and it gives us a place to catch
  failures.
- **Failure isolation is the critical detail.** `base.register()` raises on a duplicate name, so
  today a plugin claiming `rain` would crash `cinemagraph --help` for every command. Each plugin
  load gets wrapped: a broken or colliding plugin logs a warning and is skipped, never takes the
  CLI down.
- Docs: a "Writing an effect plugin" section, plus a minimal working example.
- Tests: registration via a synthetic entry point; a plugin that raises on load; a plugin whose
  name collides with a built-in. The last two are the ones that matter.

**Done when:** `pip install <some-effect-package>` makes a new effect appear in
`cinemagraph from-photo --help`, and a deliberately broken plugin leaves the CLI fully working.

**Effort:** one to two days.

---

## Sequencing

```
W0 licensing ──> W1 env + README ──> W2 doctor ──┐
                                                 ├──> W4 setup tab
                        W3 images + release ─────┘

W5 plugins — independent, and deliberately unscheduled
```

W0 blocks everything (legally, not technically). W1 should precede W2 so `doctor` has documented
variables to check. W3 is independent of W2 and can run in parallel if someone else picks it up.
W4 wants W3 finished so its commands are the pull-based ones. W5 depends on nothing and should
wait for real demand.

A reasonable first milestone is **W0 + W1 + W2**: that alone takes the repo from "readable by its
author" to "installable by a stranger", in about two days.

## Explicitly out of scope

- **A setup wizard.** Immich and Open WebUI have one because they are multi-user and it exists to
  create the admin account. This tool has no auth and no users; a wizard would be copying the
  form of the pattern without its reason.
- **Authentication.** A real feature, not a packaging one. If it's wanted, it's its own project
  with its own design note — not something to bolt on during an open-source push.
- **A plugin registry or marketplace.** See W5. Entry points and pip cover the need without us
  operating a distribution channel or a security review process.
- **Generic backend plugins.** An `ImageGenerator` is a URL plus a request shape. The useful
  generalization is supporting an **OpenAI-compatible endpoint via config**, which covers LocalAI,
  vLLM, Ollama, and most hosted APIs without anyone writing code. That's a small adapter against
  the existing port, not a plugin subsystem — worth doing, but as a backend feature rather than
  part of this plan.

## Decisions

Taken 2026-08-21. Recorded here so W3 and the README don't relitigate them.

1. **License: Apache-2.0.** Chosen over MIT for the explicit patent grant (§3) — this project sits
   in diffusion-model and video-codec territory, where patents genuinely exist — and for the
   contribution terms in §5, which make a drive-by PR's licensing unambiguous without a CLA. The
   cost over MIT is a longer file and the `NOTICE` convention; adoption-wise they're equivalent.
   AGPL-3.0 was the considered alternative (Immich's and Nextcloud's choice) and was rejected:
   it protects against someone running a hosted commercial version, which isn't a concern for a
   personal creative tool, and it deters some corporate users outright. **Applied.**
2. **GHCR owner: the author's personal account**, images named `cinemagraph-core`,
   `cinemagraph-machine-learning`, `cinemagraph-sound-effects`, `cinemagraph-image-generation` —
   one prefix so they sort together. An org for a solo project is ceremony, and GHCR packages can
   be transferred later if that changes. *Still outstanding: the literal GitHub handle. The repo
   has no git remote configured yet, so this cannot be filled in from the repo.*
3. **First release publishes `core` only.** The satellites are exactly where the CI disk risk
   lives (see W3), and tying the first public release to an unproven multi-GB CUDA build is how a
   release slips. `core` is small, builds in existing CI today, and covers the entire
   always-available surface — photo, video, library, assemble, the whole web UI. Satellites follow
   in the next release once runner disk has been measured; until then users build them locally,
   which is exactly today's status quo, so nobody is worse off.
4. **Rename to `Stillwave`** — a **still** image plus a **wave**, both the motion and the audio.
   `cinemagraph-tool` is accurate as a repo name and forgettable as a product name, and it is now
   actively too narrow: the project does music, sound effects, image generation, and long-form
   assembly, none of which are cinemagraphs. Renaming is free today and expensive once anyone
   links to it. **Not yet applied** — a rename touches the package directory, the console script,
   every import, the image names, and the docs, so it is its own task, and this decision is the
   one most open to being overruled on taste.

## Open questions

1. **The GitHub handle** for decision 2's image names — blocks writing the W3 workflow.
2. **`ffmpeg` redistribution terms.** The Docker images bundle an `ffmpeg` binary via
   `imageio-ffmpeg`, and its license depends on how that build was configured (LGPL vs GPL). This
   affects redistributing the *images*, not this repo's source. Worth confirming before W3
   publishes anything; noted in the README's Licensing section.
