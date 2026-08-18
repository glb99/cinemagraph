# SDXL CPU offload

**Date:** 2026-08-18
**Question:** [the model-lifecycle experiment](2026-08-18-lazy-model-ttl-and-hf-cache-volumes.md) proposed `enable_model_cpu_offload()` on the SDXL pipeline as the highest-leverage next step — if it drops SDXL's resident ~7.1 GB to ~2 GB, the satellites stop being mutually exclusive and the `MODEL_TTL` becomes a nice-to-have. Does it?

## What was tried

`image-generation/app.py`'s `_get_pipe()` gained a `CPU_OFFLOAD` env var: `enable_model_cpu_offload()` instead of `_pipe.to(DEVICE)`, so both arms could be measured against the same image. Benchmarked by bind-mounting the working-tree `app.py` over `/app/app.py` in the existing container — the same trick the TTL work used, since that Dockerfile `COPY`s `app.py` *before* `pip install` and any edit otherwise triggers a full torch reinstall.

Measured per arm: time to healthy, VRAM before/at-rest/peak/after, cold and warm generation wall time, at 1024×1024 / 30 steps (the service's own defaults).

## Result

**Inconclusive — the comparison could not be run on this host.** Both arms failed, and identically:

| | `CPU_OFFLOAD=1` | `CPU_OFFLOAD=0` (baseline) |
|---|---|---|
| cold generate | 151.4 s → **HTTP 500** | 59.8 s → **HTTP 500** |
| peak VRAM | 1677 MiB | 1703 MiB |
| reached `Model loaded.` | yes | **no** |

Neither produced an image. The failure:

```
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 26.00 MiB.
GPU 0 has a total capacity of 8.00 GiB of which 4.79 GiB is free.
```

**A 26 MiB allocation refused with 4.79 GiB free is not VRAM exhaustion.** The card is fine: a fresh container on the same GPU allocates 1, 2 and 4 GiB in sequence without complaint. What is not fine is the host — `.wslconfig` caps the WSL2 VM at **10 GB**, the host has **15.3 GB total with ~2.9 GB free** and ~4.3 GB of commit remaining, so the driver cannot get host memory to back the allocations. The same condition broke the first benchmark harness outright: Git Bash could no longer `fork` ("the paging file is too small to complete the operation"), so its measurement commands never ran.

Retried at 512×512 / 20 steps to separate correctness from footprint. Same failure, so **the change is not even functionally validated** — `enable_model_cpu_offload()` is not known to produce a valid image here, and its interaction with `from_pipe()`'s shared components on the img2img path is untested.

## Verdict

- [ ] Inconclusive — blocked on host memory, not on anything about the change. Resolved by re-running with more free host RAM (or a smaller WSL2 cap so the VM stops competing with the host for it).
- [x] **Adopted, defaulting off** — `CPU_OFFLOAD` in `image-generation/app.py`, off unless explicitly set. Committed so the experiment is repeatable, not because it is believed to work. Branch `perf/sdxl-cpu-offload`.

## Notes

**Offload may be the wrong lever on this machine, and that is worth knowing before retrying.** `enable_model_cpu_offload()` keeps the weights in *system RAM* and pages each sub-model onto the GPU as it runs. It buys VRAM by spending host RAM — and host RAM is this host's binding constraint. The `Exited (137)` kills that began this whole line of work were host-RAM OOM kills, not VRAM ones. So the mechanism trades away the scarcer resource for the more plentiful one, which is backwards here even though the VRAM logic is sound in the abstract. A host with RAM to spare would see the benefit the parent experiment predicted; this one plausibly gets worse. Measure before believing either.

**One solid number did come out of it, and it closes the parent experiment's open question.** The pipeline's 7 components loaded in **43 s** from the `image-generation-hf-cache` *named volume*, against the ~6 minutes recorded through the old WSL2 bind mount (`7/7 [06:08, 52.64s/it]`). That was the measurement `MODEL_TTL=900` was waiting on — 900 s was chosen defensively because "unloading a model that costs 6 minutes to restore is worse than holding it", and at 43 s that reasoning no longer applies. **`MODEL_TTL` can drop to Immich's 300 s.** Measured under memory pressure, so if anything it understates the improvement.

Incidentally this also settles a stale claim: the volume holds ~14 GB, so the weights *are* there. The migration recorded as abandoned did happen (or the container re-downloaded them), and the compose comment describing the volumes as starting empty is now itself out of date.

**Two harness bugs, both mine, both instructive.**

The first harness was bash. Under the memory pressure SDXL creates it could not fork subprocesses, so every measurement silently produced nothing (`http_status=000`, VRAM never leaving 2 MiB) while looking like it had run. Rewritten as a single Python process with one long-lived `nvidia-smi -l` sampler instead of a fork per reading.

The rewrite then completed every measurement of a full run and threw them all away at the last step: it decoded `docker logs` with the Windows default codec, hit a progress-bar byte cp1252 cannot represent, and died with `stdout=None`. Results are now written to disk *before* logs are touched, so log parsing can never again destroy data already collected. Worth generalising: a measurement harness should persist what it has as soon as it has it.
