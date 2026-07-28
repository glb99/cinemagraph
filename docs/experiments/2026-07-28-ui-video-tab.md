# UI expansion: video/music/sound-effects tabs

**Date:** 2026-07-28
**Question:** The first UI slice covered photo rendering only. Does the same pattern (one page,
`GET /capabilities`-gated tabs, shared job-polling) extend cleanly to the other three capabilities,
and does the video tab actually work end-to-end against a live server?

## What was tried

Restructured `server/ui.py` from a single form into four tabs (Photo, Video, Music, Sound effects),
with Photo and Video always shown and Music/Sound effects hidden entirely (not shown-disabled)
unless `GET /capabilities` reports the matching flag. Factored the previously-inline photo-only
JS into two shared functions used by all four forms: `pollJob()` (the `GET /jobs/{id}` loop) and
`wireForm()` (submit handling, error display, disabling the button mid-request) — safe to share
because every `/render/*` and `/generate/*` route returns the identical `{job_id}` shape and is
polled the identical way; only the request body and which `<video>`/`<audio>` element receives the
result differ per tab.

Verified past a TestClient smoke test again: booted a real `uvicorn` server, loaded the page in the
actual browser, confirmed the nav correctly showed only Photo/Video (no ACE-Step or sound-effects
service running), clicked the Video tab, and drove its real form via the same in-page
`File`-via-`DataTransfer` technique used for the photo tab previously.

## Result

Hit a real bug in the verification method itself, not in the application: the first attempt used a
~25KB base64-encoded test video embedded directly in the JavaScript tool call, which failed with
`Failed to fetch`. Diagnosed rather than guessed: added a check reading `b64.length` inside the
page after assignment — it came back `24926`, not the actual `24904` characters written, and
`atob()` on it threw `"the string to be decoded is not correctly encoded"`. So the string was
corrupted in transit through the tool call itself (extra characters inserted somewhere), not a
`fetch()`/CSP/data-URI-length limitation — confirmed separately that small and medium-sized data:
URIs fetch fine in this environment. Fix: generated a much smaller synthetic test video directly
with `imageio`/`numpy` (8 tiny frames, ~2KB file, ~2.6KB base64) instead of using the project's
`examples/make_test_clip.py` default size, safely under whatever threshold caused the corruption
(the earlier photo-tab verification's ~6.5KB base64 image had worked fine, consistent with this
being a size-dependent transport issue rather than a fetch/CSP one).

With the smaller video, the real form worked first try: job submitted → polled → `done` → the
`<video>` element's `src` populated → downloaded that exact URL separately with `curl` and
confirmed real, correctly-sized MP4 bytes (5,030 bytes) came back.

`tests/test_api_smoke.py`'s existing page-content assertion was updated to check for both
`id="photo-form"` and `id="video-form"` (the old test checked a generic `id="form"` that no longer
exists post-restructure).

## Verdict

- [x] Adopted — `server/ui.py` now has all four tabs. Photo and Video are built and verified
      end-to-end against a live server. Music and Sound effects have forms wired to their routes
      (`/generate/music`, `/generate/sound-effect`) and correct capability-gating, but haven't been
      exercised against a live ACE-Step/sound-effects backend from the UI specifically — the routes
      themselves were already validated separately (see the 07-27 audio-model-serving-research log).

## Notes

Worth remembering for any future browser-based verification here: very large literal strings
(tens of KB) passed as a single `javascript_tool` call are not reliable — they can get silently
corrupted in transit. Prefer generating a smaller test fixture over debugging the transport, unless
the large-payload case is specifically what's being tested.
