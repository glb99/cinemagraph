# ACE-Step "generation timed out after 150 polls" on a 90s track: diagnosed and fixed

**Date:** 2026-08-01 (initial), follow-up 2026-08-03
**Symptom reported:** a 90-second music generation job failed with
`RuntimeError: ACE-Step generation timed out after 150 polls.` (`ACEStepAdapter.generate()`,
`generation_adapters.py`).

## First-round diagnosis (2026-08-01): poll budget and wasted batch compute

Same discipline as everywhere else in this project: checked the actual container, not
assumed the timeout meant "the server is broken."

`docker ps` showed the `acestep` container healthy and running. `docker logs` showed every
single `/query_result` poll during the timed-out job returned `200 OK` -- the HTTP layer
was fine throughout; the job's own status inside those 200s just never reached `1` (done)
or `2` (failure) within the 150-poll budget.

**Confirmed the server wasn't hung, only slow (at the time)** -- read PID 148's
`/proc/148/stat` `utime` field twice, 5 seconds apart, on a request that outlived the old
timeout: `92930 -> 93468`, i.e. still consuming ~100% of a core continuously.

Two real, independent fixes were made and confirmed correct on their own terms (neither
was a red herring, but neither turned out to be the actual root cause of the worst
failures -- see the 2026-08-03 follow-up below):

1. **Raised `MUSIC_POLL_MAX_ATTEMPTS` from 150 to 400** (800s total,
   `generation_adapters.py`) -- ACE-Step's own server logs its LM "thinking" step's own
   budget at startup ("Setting constrained decoding max_duration to 480s based on GPU
   config (tier: tier3)"); 300s was never going to reliably cover that plus the
   diffusion+VAE-decode steps after it.
2. **Set `batch_size: 1`** on every `/release_task` call -- ACE-Step's own default is `2`
   (confirmed in its `API.md`), but `ACEStepAdapter` only ever keeps `result[0]`, so the
   second candidate was pure wasted GPU time on every request, independent of anything
   else in this investigation. Still correct, still in place.

## Second-round diagnosis (2026-08-03): the real root cause was the LM backend

The above fixes did not actually solve the worst case. A real 90-second,
`thinking=true` request was retried (batch_size=1, 800s budget, freshly restarted
container) and **still never finished after 15+ minutes** -- confirmed still actively
computing via the same `/proc/<pid>/stat` `utime`-sampling technique (climbing, not
frozen), so this wasn't a hang in the "deadlocked" sense, but a severe, unbounded
slowdown the poll-budget fix couldn't paper over.

**The actual breakthrough came from an external data point, not more internal
measurement**: the project owner reported that running ACE-Step's own Gradio UI
*natively* (no Docker, no our adapter) with `thinking=true`, batch of 2, and an even
*longer* 120-second track completed without issue. Same GPU, same model, longer duration,
thinking on, batch of 2 -- and no stall. That ruled out duration, `thinking` mode itself,
and batch size as the cause, and pointed at something specific to *how this project's
Docker deployment runs ACE-Step* versus a native run.

Two hypotheses were checked in order, against the real container, not guessed:

1. **`shm_size` (2gb, common Docker+PyTorch/vLLM footgun)** -- raised to 8gb, container
   recreated, confirmed via `df -h /dev/shm` inside the container. Re-ran the exact
   stalled case: it progressed further than before (reached `"stage": "Phase 2:
   Generating audio codes"`, `progress: 0.5`) but then **stalled at that exact point** --
   `progress` and the server's own tqdm step timer (`[00:03<?]`) both frozen for 9+
   minutes while CPU time kept climbing (confirmed via the same double-sample
   `/proc/<pid>/stat` technique). Ruled out as the real fix; **left at 8gb anyway** as
   reasonable headroom, but it did not resolve the actual problem.
2. **LM backend: `vllm` vs `pt`.** Re-read ACE-Step's own `docker-entrypoint.sh`
   (already on disk from earlier "open the Gradio app" research this session): the
   **Gradio launch path** passes `--backend "${ACESTEP_LLM_BACKEND:-pt}"` explicitly;
   the **API server launch path** (what this project's `docker-compose.yml` runs) passes
   no backend flag at all. Traced the real selection logic in the ACE-Step source
   (`acestep/api/startup_llm_init.py` -> `acestep/gpu_config.py`'s `resolve_lm_backend`):
   with no `ACESTEP_LM_BACKEND` env var set (our compose file's actual state), it falls
   back to the GPU tier's own `recommended_backend`, which resolved to `vllm` on this
   card (confirmed in the container's own boot log: `_initialize_5hz_lm_vllm`). A
   same-request `lm_backend: "pt"` field in `/release_task`'s JSON body was tried first
   and made no difference -- confirmed via the source (`llm_inference.py`'s `initialize()`
   sets `self.llm_backend` once at server boot; a later per-request field can't retroactively
   swap an already-loaded model's backend).

   **Set `ACESTEP_LM_BACKEND=pt` in `docker-compose.yml`, recreated the container,
   confirmed via the boot log this time** (`_load_pytorch_model:412 - 5Hz LM initialized
   successfully using PyTorch backend on cuda`), then re-ran the *exact* request that had
   stalled indefinitely under `vllm` (90s duration, `thinking=true`, `batch_size=1`):

   ```
   elapsed=84s
   "generation_info": "Total generation time (1 song): 53.40s\n
     - LM phase (1 song): 43.82s\n- DiT phase (1 song): 9.59s"
   ```

   Completed cleanly. This is the real fix, and it also fully explains the project
   owner's own native-Gradio observation: Gradio's own entrypoint path defaults to `pt`,
   so a native run never exercised the buggy `vllm`/nano-vllm path at all -- it was never
   a duration or batch-size difference between the two environments, it was always a
   different LM backend.

## Fix

`docker-compose.yml`'s `acestep` service now sets `ACESTEP_LM_BACKEND=pt` explicitly,
matching what Gradio's own launch path already defaults to and what's now confirmed
correct on this hardware -- `vllm` (nano-vllm, a custom third-party engine built into the
image, per the Dockerfile's own build step) has a real, reproducible stall on longer LM
sequences that plain PyTorch does not share. `shm_size: 8gb` and `MUSIC_POLL_MAX_ATTEMPTS:
400`/`batch_size: 1` (this project's adapter code) all remain in place -- each is still a
real, independently-justified improvement, just not the fix for the worst-case stall.

## Verdict

- [x] Confirmed via real process inspection (`/proc/<pid>/stat` sampled twice, twice
  over), not assumption, that the server was actively computing at every stage checked --
  ruling out a classic deadlock, and correctly pointing the investigation toward "severe
  slowdown" rather than "hang," which mattered for interpreting each subsequent test.
- [x] Took an external, contradicting data point (native Gradio succeeding at an even
  longer duration) seriously enough to re-open a "fixed" investigation, rather than
  dismissing it or rationalizing it away.
- [x] Found the real root cause by reading ACE-Step's own source
  (`docker-entrypoint.sh`, `startup_llm_init.py`, `gpu_config.py`, `llm_inference.py`),
  not by guessing at request parameters -- and confirmed the fix with a clean, isolated,
  timed before/after test against the exact request that used to stall indefinitely.
- [x] `shm_size`/poll-budget/`batch_size` changes from the first round are kept (each
  independently justified) but explicitly not credited as "the fix" -- they didn't
  resolve the real case, and claiming otherwise would misattribute the cause.
