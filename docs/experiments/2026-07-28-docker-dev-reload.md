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

## Follow-up: `--reload` reverted after a real crash (2026-07-29)

The `--reload` half of this setup caused a genuine outage, discovered through real use rather than
testing: rendering a large real-world photo (not the tiny test fixtures) pushed container memory
into the multi-GB range, and at some point during a reload cycle `watchfiles`' rust-based watcher
failed with `WatchfilesRustInternalError: error in underlying watcher: Cannot allocate memory (os
error 12)` -- a known, if intermittent, failure mode for this watcher under memory pressure or on
certain bind-mount backends. Since the watcher runs as part of uvicorn's own reloader process, that
crash took the entire server down, not just the reload mechanism -- every in-flight and subsequent
request failed until the container was manually restarted.

Separately, this session also found that the *bind mount itself* (independent of `--reload`) can
break if the host-side directory it points at is deleted while a container has it mounted -- caused
directly by an earlier `rm -rf data/` cleanup command run against a live container. That corrupted
`/data`, and because `_job_dir()`'s `mkdir()` call runs synchronously inside an async route handler
(a fine assumption under normal conditions, since `mkdir` is normally sub-millisecond), the broken
mount caused that call to hang, freezing the entire event loop -- not just that one request. Fixed
by recreating `./data` and restarting the container; noted here as a reminder not to delete a
directory a running container has bind-mounted.

Given `--reload` was the piece that actually crashed, and the underlying bug that prompted this
whole workflow (a stale image, not a lack of live-reload) is fully solved by the bind mount alone,
`--reload`/`--reload-dir` were removed from `docker-compose.override.yml`, leaving just the mount.
A source edit now needs one `docker compose restart core` to take effect instead of reloading
automatically -- a restart is fast and unambiguous, versus a watcher process that can silently take
the whole server down under exactly the kind of load this tool's actual job (video/audio rendering)
routinely produces.

Verified the replacement behavior end-to-end: edited `server/ui.py`'s `<title>` on the host with the
container running, confirmed the live page did *not* pick it up (no watcher), ran
`docker compose restart core`, confirmed it did -- then reverted and confirmed the same both ways.
Also confirmed no `WatchFiles`/reload log lines appear at all anymore.

### Verdict (follow-up)

- [x] Adopted -- bind mount kept, `--reload` removed. `docker compose restart core` is now the
      documented way to pick up a source change.

## Second follow-up: replaced with Compose Watch, the official mechanism (2026-07-29)

**Question:** the previous fix traded automatic reload for a manual restart, purely to avoid the
crash. Is there a way to get automatic reload back without reintroducing that risk? Researched
Docker's own documentation before hand-rolling anything further, rather than assuming the two were
fundamentally in tension.

## What was tried

`docs.docker.com/compose/how-tos/file-watch/` documents Compose's own `develop.watch` mechanism,
confirmed via direct fetch of the official reference page
(`docs.docker.com/reference/compose-file/develop/`) for exact schema (action types `sync`, `rebuild`,
`sync+restart`; minimum Compose version 2.22-2.23 depending on action -- this environment runs
2.30.3, well above). The critical fact, confirmed directly from the docs rather than assumed: this
watching runs **on the host, via the Compose CLI itself** -- not as a process inside the container.
That's a structural difference from `uvicorn --reload`'s `watchfiles`, which runs inside the same
container process as the app and is exactly what crashed before.

Replaced `docker-compose.override.yml`'s bind mount with:

```yaml
develop:
  watch:
    - action: sync+restart
      path: ./src
      target: /app/src
    - action: rebuild
      path: ./pyproject.toml
    - action: rebuild
      path: ./uv.lock
```

`sync+restart` subsumes what the bind mount + manual restart did (copies the changed file in, then
restarts the container so uvicorn picks it up), but automatically, and `rebuild` automates the
`docker compose build core` step for dependency changes too. Confirmed `.dockerignore`'s existing
patterns (`__pycache__/`, `*.pyc`, etc.) apply automatically as `ignore` content, so nothing extra
was needed there.

## Result

Started via `docker compose watch core`. Confirmed the mount was genuinely gone
(`docker inspect`'s `Mounts` showed only `/data`, not `/app/src`) -- sync is a distinct mechanism, not
a mount. Edited `server/ui.py`'s `<title>` on the host: the live page updated and the container's
uptime reset (`Up 8 seconds`) with zero commands run beyond the edit itself -- confirmed via
`docker logs` showing a clean shutdown/restart cycle, no `WatchFiles`/reload lines at all (there's no
in-container watcher to log anything).

Then reproduced the *exact* scenario that caused the original crash, not a smaller/safer version of
it: rendered a large real photo (not a tiny fixture) through the API while `docker compose watch` was
running, watching `docker stats` throughout. Memory climbed to 3.7GB+ -- the same order of magnitude
that crashed the old `--reload` setup. This time: the render completed successfully
(`"status":"done"`), the container never restarted, `docker ps` still showed the original uptime, and
`docker logs` had no errors, crashes, or `WatchfilesRustInternalError` anywhere. The structural
argument (watcher runs outside the container, so it can't be starved by the container's own memory
pressure) held up under a direct repro, not just in theory.

## Verdict

- [x] Adopted -- `docker-compose.override.yml`'s bind mount replaced by `develop.watch`
      (`sync+restart` for `src/`, `rebuild` for `pyproject.toml`/`uv.lock`). `docker compose watch
      core` (or `docker compose up --watch`) is now the documented way to develop against this
      container -- fully automatic again, without the crash risk that using `--reload` for the same
      goal (avoiding a manual restart) had reintroduced.

## Notes

This supersedes the previous two approaches in this file (bind mount + `--reload`, then bind mount +
manual restart) but both are kept above as-written -- each was a real, working intermediate state
that the next section's reasoning depends on understanding.
