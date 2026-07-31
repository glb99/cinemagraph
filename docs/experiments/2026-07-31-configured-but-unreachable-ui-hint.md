# "Configured but not running" hint in the web UI (§5.10)

**Date:** 2026-07-31
**Question:** running just the `core` container, `GET /capabilities` correctly reported
`image_generation` as available (Gemini configured), but Music/Sound effects' tabs were
simply absent from the web UI even though their env vars (`ACESTEP_URL`,
`SOUND_EFFECTS_URL`) were set in `docker-compose.yml` -- their containers just weren't
started. Is auto-deploying the right container from the UI on selection a good idea?

## Considered and rejected: auto-deploy on selection

Would require `core` to hold the Docker socket to shell out `docker compose up` -- a real
privilege-escalation surface (container escape risk) for a personal, single-user tool.
Also: this project's single 8GB GPU can only usefully run one GPU-heavy service at a time
(already observed: real contention running `image-generation` and `acestep`
simultaneously earlier this session) -- auto-deploy-on-select would also need to
auto-*stop* whatever else is running to free the GPU, a real orchestration layer. Not
justified against §6's own ceiling for this project (`docker-compose`, nothing heavier
without a real trigger like a hosted/multi-machine deployment).

## What was built instead: a cheap, additive UI hint

- `server/schemas.py`: `CapabilitiesResponse` gained `configured: dict[str, bool]`,
  purely additive (existing bool fields unchanged).
- `server/app.py`'s `/capabilities` route: populates `configured` from
  `bool(settings.<x>_url)` per satellite (`image_generation`'s own entry is true if
  *either* `IMAGE_GENERATION_URL` or `GEMINI_API_KEY` is set, matching how its `available`
  bool already unions the two backends).
- `server/ui.py`: `loadCapabilities()` now shows a tab if it's `always`, `available`, OR
  `configured` (previously: `always` or `available` only). A configured-but-unavailable
  tab gets a `⚠` suffix on its nav label and a persistent amber banner inside its own
  section naming the exact command to run (e.g. `docker compose --profile audio up
  acestep`), read from a new `startCommand` field per `TABS` entry. A tab that's neither
  configured nor available still stays fully hidden -- nothing to hint about without
  redeploying.

## Verification

- `uv run pytest`: 87 passed (one existing capabilities test updated for the new field,
  one new test confirming `configured=True, music_generation=False` for a URL that's set
  but unreachable, distinct from the all-unconfigured case), 2 deselected.
- **Real browser check**, not just JSON assertions: rebuilt and recreated the real `core`
  container (`docker compose build core && docker compose up -d core`) with
  `ACESTEP_URL`/`SOUND_EFFECTS_URL` set but neither `acestep` nor `sound-effects`
  containers running. Loaded the actual web UI: "Music ⚠" and "Sound effects ⚠" now
  appear in the nav (previously invisible entirely). Clicked into Music: the banner reads
  "Music is configured but not reachable right now -- its container probably isn't
  running. Start it with: docker compose --profile audio up acestep" -- exact, actionable,
  and correct for this specific deployment.

## Verdict

- [x] Rejected auto-deploy-on-select as a real architectural change with a bad
  cost/benefit for a single-user, single-GPU personal tool.
- [x] Built and verified (real API response + real browser) the cheap alternative: a
  `configured` signal the UI uses to hint instead of hiding.
- [x] No new infrastructure, no polling -- reuses the existing single `GET /capabilities`
  call already made on page load.
