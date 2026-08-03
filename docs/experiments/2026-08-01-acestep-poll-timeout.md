# ACE-Step "generation timed out after 150 polls" on a 90s track: diagnosed and fixed

**Date:** 2026-08-01
**Symptom reported:** a 90-second music generation job failed with
`RuntimeError: ACE-Step generation timed out after 150 polls.` (`ACEStepAdapter.generate()`,
`generation_adapters.py`).

## Diagnosis, against the real running container

Same discipline as everywhere else in this project: checked the actual container, not
assumed the timeout meant "the server is broken."

`docker ps` showed the `acestep` container healthy and running. `docker logs` showed every
single `/query_result` poll during the timed-out job returned `200 OK` -- the HTTP layer
was fine throughout; the job's own status inside those 200s just never reached `1` (done)
or `2` (failure) within the 150-poll budget.

**Confirmed the server wasn't hung, only slow** -- read PID 148's `/proc/148/stat` `utime`
field twice, 5 seconds apart, on a request that outlived the old timeout: `92930 -> 93468`,
i.e. still consuming ~100% of a core continuously. A genuinely deadlocked/frozen process
wouldn't show climbing CPU time. `nvidia-smi` showed 0% GPU utilization at the same moment
-- consistent with the LM "thinking" step, which is CPU-bound (tokenization + constrained
decoding), not the GPU-bound diffusion step that comes after it.

**Found the actual root cause in the container's own startup log**, not guessed:

```
Setting constrained decoding max_duration to 480s based on GPU config (tier: tier3)
```

The host GPU (RTX 4060 Laptop, 8GB VRAM) gets classified `tier3` by ACE-Step's own
`gpu_config` module -- its lowest tier -- and ACE-Step's own server explicitly budgets up
to **480 seconds** for the LM "thinking" step alone on that tier. `ACEStepAdapter`'s poll
budget was `MUSIC_POLL_INTERVAL_SECONDS=2.0 * MUSIC_POLL_MAX_ATTEMPTS=150` = **300 seconds
total** -- shorter than what the server's own hardware-tier logic says it may legitimately
need, before the diffusion+VAE-decode steps that come after "thinking" even start. A real
successful request, logged separately this session, took ~77s past the end of its own
thinking step -- so 300s total was never going to reliably cover `480s (thinking) + ~80s
(generation/decode)` on this hardware.

## Why this wasn't caught by the earlier cold-start timeout note

`ACEStepAdapter`'s existing comment on `query_result`'s own per-call `timeout=90.0`
(distinct from this poll-*count* budget) already documented a related-but-different
cold-start problem: a *single* `/query_result` call blocking synchronously through model
loading. That fix (bumping one HTTP call's timeout from 30s to 90s) didn't touch the
*total* number of polls allowed, which is the budget that actually mattered here -- a
long-but-steadily-progressing "thinking" step on a low VRAM-tier GPU, not a slow individual
poll.

## Fix

Raised `MUSIC_POLL_MAX_ATTEMPTS` from 150 to 400 (800s total, `generation_adapters.py`) --
real margin above the documented 480s thinking-step floor plus the observed ~80s of
generation/decode after it, not just "make the number bigger." Left
`MUSIC_POLL_INTERVAL_SECONDS` unchanged (2.0s -- polling cadence wasn't the problem).

A duration- or GPU-tier-aware timeout (e.g. reading the server's own advertised
`max_duration` instead of hardcoding a client-side guess) would be more precise, but wasn't
done now -- no evidence yet that a fixed, generous budget is insufficient in practice, and
guessing at a formula without a second real failure to calibrate against would be exactly
the kind of premature abstraction this project avoids.

## Verdict

- [x] Confirmed via real process inspection (`/proc/<pid>/stat` sampled twice), not
  assumption, that the server was actively computing, not hung/deadlocked.
- [x] Found the real root cause in the server's own startup log (GPU-tier-based
  `max_duration`), not guessed from symptoms alone.
- [x] Fix addresses the actual mismatch (client poll budget shorter than the server's own
  documented worst case for this hardware tier), not just "increase a number until it
  works."
- [ ] Whether 800s comfortably covers every real request on this GPU tier (not just the
  one case observed) is still to be confirmed by future real usage -- flagged, not assumed.
