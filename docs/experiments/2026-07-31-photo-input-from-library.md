# Picking the Photo tab's input from the library instead of uploading

**Date:** 2026-07-31
**Question:** the project owner wants to source the Photo tab's input image from
already-cataloged library assets (reference photos, generated images) instead of always
re-uploading a fresh file.

## Scope, confirmed with the project owner (via AskUserQuestion, all recommended)

- Photo tab only (not Video) -- matches "for adding the effects" (the Photo tab's job).
- Coexists as an alternative to upload, not a replacement -- pick exactly one.
- Web UI only -- the CLI already works with any file path, including a library asset's own
  stored path (`cinemagraph library show <id>`).

## What was built

- `server/app.py`'s `/render/photo`: `input_file` became optional
  (`UploadFile | None = File(None)`), a new `input_asset_id: str | None = Form(None)` added.
  Validates exactly one of the two is given (422 otherwise) -- same "pick one or the other"
  pattern the route already used for `mask`/`mask_prompt`. When `input_asset_id` is given,
  resolves via `asset_library.get()` (422 on a missing id) and uses the asset's own stored
  path directly -- no copy into the job directory needed, since it's already a stable file
  on disk.
- `server/ui.py`'s Photo tab: a new asset picker below the file input ("...or pick an
  existing photo/image from the library instead of uploading"), fetching `GET /library`
  once and filtering to image extensions (jpg/jpeg/png/webp) client-side -- same
  extension-based bucketing approach the Assemble tab's pickers already use. Selecting a
  library asset clears the file input (and vice versa), so the two inputs can't both be set
  at submit time.

## Verification

- `uv run pytest`: 113 passed (4 new -- accepts `input_asset_id`, rejects neither/both,
  rejects an unknown id), 2 deselected.
- `scripts/golden_check.py`: unaffected.
- Real browser check against a live rebuilt `core`: added a real photo to the library via
  the API, opened the Photo tab, confirmed it appeared in the picker, selected it, picked
  an effect, submitted -- job completed, preview played. Test assets cleaned up afterward
  (pre-existing library entries from earlier sessions left untouched).

## Verdict

- [x] Built exactly to the confirmed scope -- Photo tab only, alternative not replacement,
  web UI only.
- [x] Verified end to end in a real browser against a real running container.
