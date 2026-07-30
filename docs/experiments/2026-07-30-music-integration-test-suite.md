# Building the first post-deployment integration test suite (music generation)

**Date:** 2026-07-30
**Question:** `docs/DESIGN.md` sec 3.7/5.9 documented a plan for real, repeatable
post-deployment integration tests, starting with music generation. Does it actually work
end to end against a real deployed stack?

## What was built

- `pyproject.toml`: registered an `integration` pytest marker, `addopts = "-m 'not
  integration'"` so the default `uv run pytest` run never picks these up (needs a live
  GPU-backed stack, takes real minutes -- shouldn't slow down or break the fast/hermetic
  default suite).
- `tests/integration/conftest.py`: a session-scoped real `httpx.Client` against
  `CINEMAGRAPH_TEST_BASE_URL` (default `http://localhost:8000`), and a `capabilities`
  fixture that skips the whole file with a clear reason if `core` isn't reachable at all.
- `tests/integration/test_music_live.py`: an autouse fixture skips (not fails) if
  `music_generation` isn't reported by `/capabilities` specifically. Two tests:
  - the plain happy path (`POST /generate/music` -> poll `GET /jobs/{id}` -> download and
    validate the real MP3 -> confirm library registration),
  - `instrumental=true` with lyrics text deliberately included, checking the override
    actually reached ACE-Step (`provenance.lyrics == "[Instrumental]"`) -- the same
    manual check already done by hand in
    `docs/experiments/2026-07-29-music-generation-live-verification.md`, now automated.
- Audio validity check is deliberately not bit-exact (this is generative, non-deterministic
  output, not `golden_check.py`'s pipeline): checks the ID3 tag is present and the byte
  size roughly tracks the requested duration at ACE-Step's known 128kbps CBR output
  (confirmed via `file` throughout this project's history), with generous tolerance.

## Verification

1. **Default run excludes them correctly**: `uv run pytest` -> `73 passed, 2 deselected`.
2. **Explicit run skips gracefully with nothing deployed**: `uv run pytest -m integration`
   with no containers up -> both tests `SKIPPED` with a clear reason, not an error/hang.
3. **Explicit run against a real live stack**: `docker compose --profile audio up -d
   acestep core`, waited for `acestep` to fully eager-load (`llm_initialized: true`),
   confirmed `GET /capabilities` reported `music_generation: true`, then
   `uv run pytest -m integration` -> both tests `PASSED` in ~87s total against the real
   GPU-backed instance.

## Verdict

- [x] Built and verified exactly as designed in `docs/DESIGN.md` sec 3.7/5.9: no dedicated
  test container (hits the already-published `core` port directly, same as every manual
  `docs/experiments/` curl session this project has done), marker-excluded from the
  default run, skips gracefully rather than failing when nothing's deployed.
- [x] Confirmed working against a real deployment, not just unit-tested in isolation --
  both the skip path (nothing running) and the real path (live GPU-backed ACE-Step) were
  exercised, not assumed.

Sound-effect and image generation follow this same shape later, once there's a concrete
reason to (per §5.9's own stated sequencing) -- not built speculatively ahead of that.
