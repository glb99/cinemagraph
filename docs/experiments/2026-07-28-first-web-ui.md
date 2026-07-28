# First web UI slice (photo rendering)

**Date:** 2026-07-28
**Question:** With the API/CLI parity gap just closed, is it worth starting a thin web UI, and
if so what's the smallest real slice that proves the pattern without over-scoping?

## What was tried

Built `server/ui.py` (`INDEX_HTML`, one self-contained HTML file, inline CSS/JS, no build step)
served via a new `GET /` route in `server/app.py`. Scope: photo rendering only (upload, effect
checkboxes from `GET /effects`, optional mask upload or `mask_prompt`, duration/fps/speed, submit,
poll `GET /jobs/{id}`, preview via `GET /jobs/{id}/file`). `mask_prompt` only appears when
`GET /capabilities` reports `semantic_mask: true` — the same gating every other optional-service
consumer already uses.

Deliberately chose a plain Python string over static files: non-`.py` assets need explicit
`package-data`/`MANIFEST.in` config to actually ship in a real (non-editable) build, which is
exactly the bug the `api/`→`src/api/` move caught for this project already (confirmed via
inspecting an actual `uv build` wheel back then). A string constant in `ui.py` is packaged the
same as any other module, no separate config to get right or forget — verified again here by
checking `server/ui.py` is present in a real wheel, not just resolvable in editable dev.

Verification went past a TestClient smoke test: booted a real `uvicorn` server, loaded the page
in the actual in-app browser (not curl), and drove the page's *own* JavaScript rather than
bypassing it — constructed an in-page `File` object via `DataTransfer` (the standard trick for
testing file inputs without a native OS picker), checked an effect checkbox, dispatched the
form's real `submit` event, and read the DOM afterward.

## Result

Worked first try. The real render flow: job submitted → status text updated to "running" →
polling loop (real `setTimeout`-based, not faked) → status flipped to "done" → `<video>` element's
`src` populated with the job's file URL → downloaded that URL directly with `curl` afterward and
confirmed 200 / real MP4 bytes (228,347 bytes for a 1s/10fps dust-effect render). Also drove the
client-side mask/`mask_prompt` mutual-exclusivity check (mirrors the API's own 422 rule) through
the real form and confirmed the correct error message renders in the page, not just in a unit
test of the validation logic in isolation.

`tests/test_api_smoke.py` gained one test (`GET /` returns 200, `text/html`, contains the expected
page markers) — deliberately light, since the interesting behavior (the JS driving real API calls)
isn't something a TestClient-only test can meaningfully cover; that's what this session's live
browser check was for.

## Verdict

- [x] Adopted — `server/ui.py` + `GET /`. Photo rendering only; video/music/sound-effect UI are
      separate follow-up slices, not started.

## Notes

The in-page `File`-via-`DataTransfer` construction trick is worth remembering for any future UI
verification work here: it lets a real file-upload flow be driven end-to-end from an automated
browser session without needing OS-level file-picker automation, which this environment's browser
tooling doesn't support directly.
