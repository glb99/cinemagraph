# Audio/ML model serving research: ACE-Step, Stable Audio Open, CLIPSeg

**Date:** 2026-07-27
**Question:** Three candidate models for future capabilities (music generation, sound-effect
generation, semantic masking) — do any of them ship a ready-to-use server/image, or does
each need a wrapper built from scratch? Does that change how they should be integrated?

## What was tried

Checked each project's actual repo/packaging rather than assuming:
- ACE-Step-1.5 (already cloned locally): has `acestep-api` console script, own
  `Dockerfile`/`docker-compose.yml`, publishes `ghcr.io/ace-step/ace-step-1.5:latest`.
- Stable Audio Open (`stable-audio-tools`, used by the local `audio-effect-generation`
  experiment): confirmed via web search — real PyPI package, but no official Dockerfile
  or API server; only community-maintained Docker wrappers exist (unaudited, no
  accountability equivalent to ACE-Step's maintainer-published image).
- CLIPSeg (`timojl/clipseg`): fetched the repo directly — notebooks/research code only,
  no Dockerfile, no server. Also confirmed via search: not a real PyPI package (only
  `pip install git+https://github.com/timojl/clipseg.git`), unlike the `transformers`
  library's own CLIPSeg support, which *is* a real PyPI package.
- Read ACE-Step's actual API contract (`docs/api/API.md` via its `acestep-docs` skill)
  rather than guessing: it's an async job queue (`release_task` → poll `query_result` →
  `/v1/audio`), not a simple prompt-in/audio-out call.

## Result

Three different situations, not one:
- ACE-Step: nothing to build, only a client. Implemented (`POST /generate/music`).
- Stable Audio Open: no server exists anywhere (official or trustworthy third-party), but
  the local generation code already works (proof: committed `.wav` output). Wrapper is a
  well-scoped, small remaining task — not started.
- CLIPSeg: no server exists, and unlike Stable Audio Open, the generation quality itself
  hasn't been validated for this project's use case yet. Furthest from done of the three.

## Verdict

- [x] Adopted — ACE-Step client built and tested against the "service absent" path
      (`api/app.py`'s `_run_music_job`, `tests/test_api_smoke.py`). Real end-to-end
      validation against a *running* ACE-Step server is still needed — not done in this
      session (no GPU/server available here); do that before considering this fully proven.
- [x] Adopted — CLIPSeg must be loaded via `transformers`, not `timojl/clipseg` directly,
      once §5.3 gets built.
- [ ] Inconclusive — Stable Audio Open wrapper: not built. Next experiment, if picked up:
      wrap `generate_rain_stableaudio.py`'s logic in a minimal FastAPI service and confirm
      the "load once, don't reload per-request" change doesn't affect output quality.

## Notes

`api/_external_service.py`'s shared client helper (`call_optional_service`/
`service_available`) came out of this — used by both the CLIPSeg proxy and the ACE-Step
client, since both live in `api/`'s single codebase and don't cross the isolation boundary
that keeps the *services themselves* from sharing code. See `docs/DESIGN.md` decision log.
