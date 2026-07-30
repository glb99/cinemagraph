# Building scripts/verify_music_deploy.py

**Date:** 2026-07-30
**Question:** should test-running at deploy time be automated, and if so, how (docs/DESIGN.md
sec 3.7/5.9 already covers whether integration tests should run in CI -- this is the
narrower follow-up: bundling the manual up/wait/test/teardown cycle already done by hand
into one script)?

## What was built

`scripts/verify_music_deploy.py`: `docker compose --profile audio up -d acestep core` ->
poll both `acestep`'s own `/health` and `core`'s `/capabilities` until both report actually
ready (not just "container started") -> run `uv run pytest -m integration` -> tear down
regardless of outcome (`finally`, matching the project's standing "never leave a `docker
compose` thing running" rule). Same category of tool as `golden_check.py`: a deliberate,
manually-invoked script, not wired into any automatic trigger -- this project has no CI/CD
pipeline to gate, and these tests need a real GPU no typical CI runner has anyway.

## A real bug found by actually running it (twice)

First run's output made no chronological sense: subprocess output (`docker compose`'s own
lines, pytest's own test session) appeared in the right relative order, but every one of
the script's *own* `print()` calls -- the `$ docker compose ...` command echoes, every
"waiting -- ..." message from the poll loop -- were completely missing from where they
should have appeared, then all showed up bunched together at the very end of the file,
after the teardown's own output. Ran it a second time (fresh log file, ruling out a stale
file) and got the exact same pattern -- not a fluke.

Root cause: Python's `print()` is fully block-buffered (not line-buffered) whenever stdout
isn't a real terminal -- true here since the script's output was piped through `tee`/a
background-task log capture, which is the normal way this script actually gets invoked
(not interactively at a bare terminal). `subprocess.run()`'s child processes (`docker
compose`, `pytest`) have their own separate stdio and flush on their own schedule
regardless of the parent's buffering, so their output streamed through in real time while
every one of the script's own `print()` calls sat queued in Python's stdout buffer until
the whole process exited, at which point they all flushed at once, badly out of order
relative to everything else. Not a correctness bug -- the script's actual logic (up ->
wait -> test -> down) executed in the right order every time, and both runs' pytest
results were genuinely correct (`2 passed`) -- but confusing enough to make it hard to
tell an interleaved-execution bug from a display artifact until root-caused, and it would
have made a real failure's cause much harder to diagnose live.

Fixed by adding `flush=True` to every `print()` call in the file. Re-ran a third time:
output appeared in correct chronological order top to bottom (up -> waiting messages ->
success -> pytest's own real-time output -> down), confirming the fix.

## Verdict

- [x] Built and verified against a real deployed stack, three times -- not assumed correct
  from the first (misleading) run's `exit code 0`.
- [x] Found and fixed a real, if cosmetic, output-ordering bug along the way (missing
  `flush=True` on a subprocess-heavy script's own prints) -- worth remembering for any
  future script in this project that mixes `print()` with `subprocess.run()` and might run
  under a redirected/piped stdout, which is most of them once run via automation rather
  than an interactive terminal.
