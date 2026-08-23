# UI redesign: stop hiding features, name the engine choice

**Date:** 2026-08-23
**Question:** three complaints, one redesign — the local-vs-hosted model choice was confusing,
first-run setup let you configure nothing, and tabs appearing and disappearing with satellite
availability made the app feel unstable. Could one pass fix all three without a rewrite?

## What was tried

Explored on a design canvas (Claude Design, `/design`) before touching code: three directions
(A: persistent left rail with status; B: command-palette-led; C: single-canvas workspace), then
artboards for the affected screens — Main, Enable/setup-inline, FromPhoto, Music, Repaint,
Activity, Setup, EnginePicker — plus two colour studies and a button-treatment sheet.

Direction A was chosen. A purple restyle was drawn and rejected; the soft-tint button treatment
from the same sheet was kept and applied everywhere.

Implemented in three slices, each standing alone:

1. **Shell + buttons.** `lib/tabs.ts` gained groups, icons and `tabStatus()`; `__root.tsx` became a
   244px rail; buttons became a wash plus a hairline `ring-inset` instead of a saturated slab.
2. **Engine picker.** `lib/engines.ts` + `components/EnginePicker.tsx`, replacing the bare
   `<select>` of adapter ids. Setup split into Hosted and On this machine.
3. **Job store + activity drawer.** `hooks/useJobs.tsx`, `components/ActivityDrawer.tsx`.

## Result

Adopted, on `feat/ui-rail-and-soft-buttons`.

The canvas is **not** in the repo (`design/` is gitignored — 2.6 MB, 2.4 MB of it a single
generated page). This file is the record; regenerate the boards from `/design` if they're wanted
again.

Two things the redesign surfaced that were bugs rather than looks:

- **Newly-visible tabs would have rendered working-looking forms that can't submit.** Hiding a tab
  had been doing double duty as the explanation for why a feature was unavailable. `ConfigHint`
  grew a second case (not-configured, as distinct from configured-but-down) in the same slice that
  stopped hiding them — the regression and its fix had to ship together.
- **Navigating away from a tab dropped its running render.** `useJobRunner` kept job state in the
  submitting component, so unmounting cancelled the poll and discarded the job id, while the render
  carried on server-side with its file still downloadable. This is what motivated the activity
  drawer: the drawer is the *visible* half, but lifting job state out of the route was the fix.

A third finding was smaller but had been costing every run: the library smoke spec had been
failing for a while on a strict-mode violation, because the page has two comboboxes both named
"Kind" (the library filter and UploadToLibrary's). Fixed on the page, not in the locator — two
identically-named controls read the same to a screen reader.

## Verdict

- [x] Adopted — `frontend/src/{lib/tabs.ts,lib/engines.ts,components/EnginePicker.tsx,components/ActivityDrawer.tsx,hooks/useJobs.tsx,routes/__root.tsx}`
- [ ] Rejected
- [ ] Inconclusive — **Setup is still read-only.** See Notes.

## Notes

**What's still open.** Setup can show the commands to run and the env vars to set, but not accept a
Gemini key, because `config.py` is deliberately environment-only and resolved once per process
(`@lru_cache`d `get_settings()`). Making it writable needs a config file, a reload path, and a
stated rule for which wins when file and environment disagree — a design decision, not a UI one,
so the tab stops at copy-able commands until it's made.

**Deployment facts vs. runtime state.** VRAM footprints, what each satellite is for, and pricing
live in the frontend (`lib/engines.ts`, next to `SERVICE_START_COMMANDS`) rather than becoming
fields on `ServiceStatus`. They're properties of a deployment, not things the running API observes
about itself. Which engines can *remix*, by contrast, is read live from `music_remix_models` —
the API already publishes exactly that subset, so a third adapter needs no frontend change. The
line is: if the API can observe it, ask the API.

**The web UI still cannot start containers**, and shouldn't. `core` runs as root with no `USER`
directive and the app has no authentication, so mounting the Docker socket would turn "anyone
reaching :8000 can spend your GPU" into "anyone reaching :8000 owns the host". Setup shows commands
to copy for that reason, not because buttons were harder.

**Testing.** Playwright now runs in CI (`test-frontend.yml`'s `e2e` job) against the bundle the API
serves rather than the Vite dev server. The earlier "browser has been closed" flakiness was the
dev-server `webServer` racing at startup, not the tests. The job runs with no satellites and no
`GEMINI_API_KEY` on purpose — the all-engines-off state is what a new contributor meets first, and
it's precisely what the setup tab and engine rows exist to explain.
