# pydantic-settings + dependency injection for server config

**Date:** 2026-07-28
**Question:** `server/`'s config had grown three inconsistent ways of reading `os.environ`
across its lifetime (a module-level constant frozen at import, a helper re-reading fresh each
call, and per-request lookups by magic string). Is adopting `pydantic-settings` + FastAPI's
documented `Depends(get_settings)` pattern actually worth it, or is that over-engineering a
personal tool's config?

## What was tried

Grepped every `os.environ`/`getenv` call in the repo first rather than guessing: exactly three,
all inside `server/` (`library.py`'s own `CINEMAGRAPH_LIBRARY_DIR` read is core-tier and separate
on purpose). Found the concrete cost of the inconsistency: `tests/test_api_smoke.py` had to
`importlib.reload(server.app)` on every fixture setup, purely because `DATA_DIR` was captured at
import time and `monkeypatch.setenv` alone couldn't reach it afterward.

Built `server/config.py`: `Settings(BaseSettings)` with `data_dir` (aliased to
`CINEMAGRAPH_DATA_DIR`, the one field that doesn't auto-map from its name), `ml_service_url`,
`acestep_url`, `sound_effects_url`, plus an `OptionalService` frozen dataclass bundling
url/display-name/env-var-name per service (previously repeated at every `call_optional_service`
call site — `run_music_job` alone passed `"ACESTEP_URL"` and `"Music generation"` three times).
`get_settings()` is `@lru_cache`d and injected via `Annotated[Settings, Depends(get_settings)]`.

Threaded `settings` through every route that needs it, and into `service.py`'s background
workflows as an explicit argument (`Depends()` can't reach a `BackgroundTask` — it isn't a request
handler — so the route resolves settings once, where injection does work, and passes it through).
`_external_service.py` now takes an `OptionalService` instead of an env-var string, and knows
nothing about the environment at all.

Replaced the `importlib.reload` fixture hack with `app.dependency_overrides[get_settings]`, the
pattern FastAPI's own settings docs recommend specifically because it wins over the `@lru_cache`.

## Result

64 tests pass (5 new, direct unit tests of `Settings` itself — env-var mapping, default
resolution, constructor-kwargs-override-environment, the `OptionalService` properties). Sanity
checked one isn't passing vacuously: temporarily broke the `CINEMAGRAPH_DATA_DIR` alias, confirmed
the corresponding test failed with a clear mismatch, then restored it.

Verified against a live server, not just TestClient: booted `uvicorn server.app:app` with
`CINEMAGRAPH_DATA_DIR` pointed at a scratch directory, rendered a photo through `POST
/render/photo`, and confirmed the job's `output_path` landed exactly under that directory —
proving the env var reaches `Settings` through real dependency injection, not just under test
overrides. Also confirmed `/capabilities` and `/mask/semantic`'s 503 degradation path still
produce the same response shape as before the refactor.

Confirmed `server/config.py` survives a real (non-editable) `uv build` wheel, same check applied
to every other new module this session.

## Verdict

- [x] Adopted — `server/config.py`, `Settings`/`get_settings`/`OptionalService`. `pydantic-settings`
      added to the `server` extra only (not core — see the tier-discipline note in `config.py`'s
      own docstring and `docs/DESIGN.md`'s decision log).

## Notes

`CINEMAGRAPH_LIBRARY_DIR` deliberately stays outside this system — `cinemagraph.library` is
core-tier and must not gain a `pydantic` dependency. The project now has two config mechanisms
(env-direct for core, `Settings` for server) rather than one, which is the correct outcome given
the tier discipline, not a half-finished migration to "eventually" unify.
