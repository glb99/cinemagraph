# SDXL CPU offload

**Date:** 2026-08-18
**Question:** [the model-lifecycle experiment](2026-08-18-lazy-model-ttl-and-hf-cache-volumes.md) proposed `enable_model_cpu_offload()` on the SDXL pipeline as the highest-leverage next step — if it drops SDXL's resident ~7.1 GB to ~2 GB, the satellites stop being mutually exclusive and the `MODEL_TTL` becomes a nice-to-have. Does it?

## What was tried

`image-generation/app.py`'s `_get_pipe()` gained a `CPU_OFFLOAD` env var: `enable_model_cpu_offload()` instead of `_pipe.to(DEVICE)`, so both arms could be measured against the same image. Benchmarked by bind-mounting the working-tree `app.py` over `/app/app.py` in the existing container — the same trick the TTL work used, since that Dockerfile `COPY`s `app.py` *before* `pip install` and any edit otherwise triggers a full torch reinstall.

Measured per arm: time to healthy, VRAM before/at-rest/peak/after, cold and warm generation wall time, at 1024×1024 / 30 steps (the service's own defaults).

## Result

**It works, and the VRAM win is larger than predicted.** Two arms against the same image, 1024x1024 / 30 steps, both producing real images:

| | `.to("cuda")` (baseline) | `enable_model_cpu_offload()` |
|---|---|---|
| **resident VRAM at rest** | **7147 MiB** | **147 MiB** |
| peak VRAM during generation | 7937 MiB | 5893 MiB |
| **host RAM free, steady state** | **~2.9 GB** | **~0.4 GB** |
| cold generate (incl. load) | 89.4 s | **60.0 s** |
| warm generate | 33-44 s | 35-71 s |

The baseline's 7147 MiB reproduces the parent experiment's figure exactly, which is the best evidence available that the harness measures the right thing. Output was byte-identical in size across arms at matched seeds, so offload is generating properly, not degrading.

**The trade is 7.0 GB of VRAM returned for ~2.5 GB of extra host RAM** — much better than one-for-one. The asymmetry comes from what each arm does after loading: the baseline reads weights into host RAM, pushes them to the GPU and *gives the host memory back* (free RAM recovers 1320 -> 2984 MB across generations), while offload keeps them in system RAM by design and stays pinned near 500 MB free.

Cold generation is *faster* under offload (60 s vs 89 s), because loading straight to CPU skips shoving 7 GB across PCIe. Warm generation is within run-to-run noise at two samples per arm; the denoise loop itself runs at a steady 1.33 it/s, so the per-step cost of paging sub-models is small at this resolution.

### What it does not fix

**Offload moves the ceiling, it does not remove it.** Freeing the card does not let two torch satellites coexist, because the second one now collides in host RAM instead: this host has 15.6 GB with the WSL2 VM capped at 10 GB (`.wslconfig`), and offload alone leaves ~400 MB free. The parent experiment's hope that offload would make the mutual exclusion "largely dissolve" is only half right -- it dissolves the *VRAM* half.

### One crash, explained

The first A/B run had the offload arm die mid-request on its second generation (`Server disconnected without sending a response`). It did not recur: a repro from a clean host ran 3/3 generations successfully with the container healthy and exit code 0. The difference was the starting condition -- in the A/B, the offload arm began immediately after the baseline arm had already drained the host, so it started with almost no free RAM rather than ~8 GB. That is the risk this feature carries on a memory-tight host, and it is why `MODEL_TTL` matters more with offload on, not less.

The evidence was nearly lost: the benchmark started containers with `--rm`, so the dead container was removed before its logs or exit code could be read. The repro deliberately does not.

### Earlier attempt, and why its numbers are void

An earlier run of this same benchmark, before ~5 GB of host RAM was freed, failed *both* arms with CUDA refusing 26-77 MB allocations while 4.8-5.8 GB of VRAM sat free. That is not VRAM exhaustion: a fresh container on the same GPU allocates 1, 2 and 4 GiB in sequence without complaint. The host could not back the allocations -- the same condition that had already stopped Git Bash from forking ("the paging file is too small"). Those numbers measure a starved host, not this change, and are kept here only as a warning that this benchmark is worthless unless the host starts with real headroom.

## Verdict

- [x] **Adopted, on by default** -- `CPU_OFFLOAD` in `image-generation/app.py`, set to `0` to restore `.to("cuda")`. Branch `perf/sdxl-cpu-offload`.
- [ ] Open -- whether two torch satellites can now actually run together, which needs host RAM this machine may not have. Offload is necessary for that, not sufficient.

## Notes

**A prediction made here before measuring, and how it came out.** The reasoning was: `enable_model_cpu_offload()` keeps weights in *system* RAM, host RAM is this machine's binding constraint (the `Exited (137)` kills that began this whole line of work were host-RAM kills, not VRAM ones), so offload would trade away the scarcer resource for the more plentiful one and plausibly make things worse. **Half right.** The direction was correct -- it does spend host RAM, and steady-state free RAM falls from ~2.9 GB to ~0.4 GB -- but the magnitude was wrong, and magnitude was the whole question: it spends ~2.5 GB to save 7.0 GB. Worth recording because the argument sounded convincing enough to have justified not running the benchmark at all.

**One solid number did come out of it, and it closes the parent experiment's open question.** The pipeline's 7 components loaded in **43 s** from the `image-generation-hf-cache` *named volume*, against the ~6 minutes recorded through the old WSL2 bind mount (`7/7 [06:08, 52.64s/it]`). That was the measurement `MODEL_TTL=900` was waiting on — 900 s was chosen defensively because "unloading a model that costs 6 minutes to restore is worse than holding it", and at 43 s that reasoning no longer applies. **`MODEL_TTL` can drop to Immich's 300 s.** Measured under memory pressure, so if anything it understates the improvement.

Incidentally this also settles a stale claim: the volume holds ~14 GB, so the weights *are* there. The migration recorded as abandoned did happen (or the container re-downloaded them), and the compose comment describing the volumes as starting empty is now itself out of date.

**Two harness bugs, both mine, both instructive.**

The first harness was bash. Under the memory pressure SDXL creates it could not fork subprocesses, so every measurement silently produced nothing (`http_status=000`, VRAM never leaving 2 MiB) while looking like it had run. Rewritten as a single Python process with one long-lived `nvidia-smi -l` sampler instead of a fork per reading.

The rewrite then completed every measurement of a full run and threw them all away at the last step: it decoded `docker logs` with the Windows default codec, hit a progress-bar byte cp1252 cannot represent, and died with `stdout=None`. Results are now written to disk *before* logs are touched, so log parsing can never again destroy data already collected. Worth generalising: a measurement harness should persist what it has as soon as it has it.
