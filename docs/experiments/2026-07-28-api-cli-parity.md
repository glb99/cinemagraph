# API/CLI parity: mask upload, per-effect overrides, /mask-preview, loop_duration

**Date:** 2026-07-28
**Question:** The API (`POST /render/video`, `POST /render/photo`) had been missing several
capabilities `make`/`from-photo` already had on the CLI: hand-painted mask upload, per-effect
override flags (`--rain-count` etc.), `--loop-duration`, and the standalone `mask-preview`
command had no HTTP equivalent at all. Does wiring these in actually work end-to-end, not just
pass a TestClient smoke test?

## What was tried

Added the missing fields/routes to `server/app.py` (mask upload on both render routes, all
9 effects' override kwargs on `/render/photo` validated through the same `cinemagraph.validation
.resolve_effect_kwargs()` the CLI already used, `loop_duration` on both, a new `POST
/mask-preview` route reusing the existing `Job`/`GET /jobs/{id}/file` machinery). Then started
a real `uvicorn server.app:app` process (not TestClient) and drove it with `curl`:

- `POST /mask-preview` on a synthetic video generated via `examples/make_test_clip.py` →
  polled the job, downloaded the result, confirmed real PNG magic bytes (`\x89PNG\r\n\x1a\n`).
- `POST /render/photo` with a hand-painted mask (uploaded PNG, not `mask_prompt`) *and*
  `dust_count=8` together → job completed.
- `POST /render/photo` with `rain_count=150` but `effect=dust` (mismatched override) → confirmed
  immediate `422` with the exact same message text `validation.py` produces for the CLI
  (`"--rain-count only applies when --effect rain is included."`), and confirmed via `curl` that
  no job was created for the request (fails before `jobs.create_job()`).

Also ran the CLI side of a bug found while doing this: `cinemagraph make ... --gif
--loop-duration 60` and `cinemagraph from-photo ... --gif --loop-duration 60` both used to crash
with a raw Python traceback (an uncaught `ValueError` from `pipeline.py`) instead of a clean
`click.UsageError` — the effect-override validation was already caught, this specific check
wasn't. Fixed by wrapping both CLI commands' final `pipeline.save_*` calls in `try/except
ValueError`, same pattern already used for `validation.resolve_effect_kwargs`. Verified both
commands now print a normal `Usage: ...` / `Error: ...` message and exit cleanly.

## Result

All of it worked first try against a live server, no debugging needed beyond the usual
Windows-path quoting friction with `curl -F`. `tests/test_api_smoke.py` gained 7 new tests
covering the same surface (mask upload, override rejection, mask/mask_prompt mutual exclusivity,
loop_duration+gif rejection on both render routes, mask-preview download) — one deliberately
broken on purpose (commented out the validation call) to confirm the test actually fails when
the behavior regresses, not just when the route 404s.

## Verdict

- [x] Adopted — `server/app.py`'s `/render/video`, `/render/photo`, new `/mask-preview`; `cli.py`'s
      `make`/`from_photo` both now catch the `loop_duration`+`also_gif` `ValueError`.

## Notes

`pipeline._LOOP_DURATION_GIF_ERROR`'s message was reworded while doing this — it used to say
`--gif`/`--loop-duration` (CLI flag syntax), which reads wrong inside a JSON `422` `detail` naming
`also_gif`/`loop_duration`. Now names the fields, reads fine in both a `UsageError` and a 422.
