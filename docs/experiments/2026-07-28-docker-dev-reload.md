# Docker dev workflow: auto-reload without rebuilding

**Date:** 2026-07-28
**Question:** The Docker image is a static build — after the last session's UI changes, running
`docker compose up` still served the old single-form page, since the image predated those edits.
Is there a way to get a local dev loop where editing `server/*.py` on the host is reflected in the
running container without rebuilding the image each time?

## What was tried

Added `docker-compose.override.yml`, which Docker Compose merges automatically with the base
`docker-compose.yml` whenever you run plain `docker compose up` — no `-f` flags, no separate
command to remember. It does two things to the `core` service only:

- Adds a bind mount, `./src:/app/src`, over the image's baked-in copy of the source.
- Replaces the container's `command` with `uvicorn server.app:app --reload --reload-dir /app/src`.

Checked Compose's actual merge semantics before relying on them rather than assuming: `volumes`
merges by target path (so this new mount coexists with the base file's `./data:/data`, doesn't
replace it), while `command` is fully replaced by the override — both behaviors needed here.

The reason this works at all: checked the local (non-Docker) `.venv`'s own `.pth` file and
confirmed `uv sync` installs the project *editable* by default — it's a bare path pointer, not a
copied wheel. The Dockerfile's own `uv sync --frozen --extra server` does the identical thing
inside the image, pointing at `/app/src`. So overlaying that exact path with a live bind mount
doesn't require any change to the Dockerfile or install step at all -- the install was already
resolving imports through that path per-request; the only thing missing was uvicorn actually
watching the directory and a live filesystem to watch.

## Result

Verified end-to-end, not just reasoned about: `docker compose build core` (already cached from a
prior build -- confirming this was in fact the stale image the user was looking at), then
`docker compose up -d core`. Log confirmed the reload watcher was scoped correctly:

```
Will watch for changes in these directories: ['/app/src']
```

`GET /` initially showed all four tabs (proving the fresh build had current code). Then, with the
container still running, edited `server/ui.py`'s `<h1>` directly on the host. Log showed:

```
WARNING:  WatchFiles detected changes in 'src/server/ui.py'. Reloading...
```

`GET /` reflected the new heading within a couple seconds, no rebuild. Reverted the edit and
confirmed the same in the other direction. Torn down afterward (`docker compose down core`); full
test suite (64 tests) still passes unaffected, since none of this touches application code.

## Verdict

- [x] Adopted — `docker-compose.override.yml`. Zero developer action needed beyond the normal
      `docker compose up`; the override is picked up automatically.

## Notes

Scoped to `core` only on purpose. `machine-learning/` and `sound-effects/` carry heavy, slow
dependencies (`torch` et al.) and are edited far less often than `server/`'s route/UI code — an
equivalent override there is easy to add later if that changes, but wasn't needed to solve the
actual problem (a stale UI in the core image).

Only source edits hot-reload this way. Changing `pyproject.toml`/`uv.lock` still requires
`docker compose build core` -- the bind mount only overlays `src/`, not the dependency install.
