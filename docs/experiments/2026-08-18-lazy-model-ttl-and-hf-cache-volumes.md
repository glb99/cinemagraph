# Lazy model TTL + Hugging Face cache volumes

**Date:** 2026-08-18
**Question:** the torch satellites kept OOM-killing each other (`Exited (137)`) whenever more than one ran. Is that a resourcing problem that needs different hardware, or a lifecycle problem that can be fixed in software?

## What was tried

First, measuring instead of guessing. The numbers on this machine:

| | |
|---|---|
| GPU | RTX 4060 Laptop, **8188 MiB** VRAM |
| Host RAM | 15.3 GB, of which Docker's WSL2 VM gets **~10 GB** |
| SDXL resident | **7147 MiB** — 87% of the card, by itself |
| Satellite images | 26.3 GB total (ML 8.3 + sfx 9.6 + SDXL 8.4) |
| Memory limits in compose | none — only GPU `reservations` |

That settles the diagnosis: the satellites were not *slightly* over budget, they were mutually exclusive. Any two torch services resident at once exceed both the VRAM and the VM's RAM. Nothing constrained them, so the kernel picked victims — the repeated `Exited (137)` on `sound-effects` and `acestep`.

Then, how comparable projects handle it. [Immich](https://docs.immich.app/install/environment-variables/) does **not** run one container per model: one ML service loads and unloads models on an idle TTL (`MACHINE_LEARNING_MODEL_TTL`, default 300s, `0` = never unload), with an optional preload. [Ollama](https://docs.ollama.com/faq) is the same idea (`OLLAMA_KEEP_ALIVE`, plus `OLLAMA_MAX_LOADED_MODELS` to cap residency). Worth knowing: there is **no hard per-container VRAM limit** available anywhere — `--gpus` selects *which* GPU, not how much memory; time-slicing and MPS share compute with explicitly no memory isolation.

So: adopt the TTL pattern. Each satellite now loads its model on the first request that needs it and unloads after `MODEL_TTL` idle seconds. `MODEL_TTL=0` keeps it resident, `PRELOAD=1` restores load-at-startup. Semantics follow Immich's (`0` = forever), *not* Ollama's, where `0` means the opposite — the two conventions genuinely disagree and this project already borrows Immich's naming elsewhere. The logic is written out separately in all three services because DESIGN.md sec 3.3 forbids them sharing code.

Second thread, found while verifying the first: a cold SDXL load took **6 minutes 8 seconds** (`Loading pipeline components...: 7/7 [06:08, 52.64s/it]`) against ~30s for the generation it precedes. Not the model's fault — `image-generation` and `sound-effects` kept their HF caches as **bind mounts** under `./data`, read through Docker Desktop's WSL2 file-sharing layer. That is the same `p9_client_rpc` pathology already diagnosed for `acestep` and fixed *there* with named volumes, never applied to these two. Both switched to named volumes.

## Result

**The TTL mechanism works.** Verified against real containers, not unit tests:

| | before | after |
|---|---|---|
| VRAM at rest | 7147 MiB | **0 MiB** |
| during generation | 7145 MiB | 7145 MiB |
| after idle TTL | 7145 MiB | **143 MiB** |
| time to healthy | ~6 min (blocked on load) | **seconds** |

`/health` stays answerable throughout, including mid-load and mid-generation. `machine-learning` does the same cycle and still returns a correct "sky" mask. 163 backend tests pass.

**The bind mount is quantifiably the problem.** Migrating the existing ~19 GB of weights into the named volumes ran at **~2.6 MB/s** (235 MB in ~90s). On this same machine pip pulls wheels at **57 MB/s** — reading the *local* cache through the WSL2 bridge is ~20× slower than downloading over the internet. Migration was abandoned as a result: re-downloading takes minutes where copying would have taken ~2 hours. The old `./data/*-cache` directories are left in place rather than deleted.

**Still open — now answered, see [the offload experiment](2026-08-18-sdxl-cpu-offload.md).** The clean measurement (load time with weights already in the named volume) was not complete at time of writing. It has since been taken as a side effect of benchmarking CPU offload: the 7 pipeline components load in **43 s** from the volume, against `7/7 [06:08]` through the old bind mount. 900 s was defensive because "unloading a model that costs 6 minutes to restore is worse than holding it"; at 43 s that no longer applies, so **`MODEL_TTL` can drop to Immich's 300 s**.

## Verdict

- [x] **Adopted** — `MODEL_TTL`/`PRELOAD` in all three satellites' `app.py`, wired through `docker-compose.yml`; `image-generation-hf-cache` / `sound-effects-hf-cache` named volumes replacing the `./data` bind mounts. Branch `refactor/lazy-model-ttl`.
- [x] Resolved — 900s can drop to 300s: the volume is populated (~14GB) and a cold component load off it takes 43s, not 6 minutes. See [the offload experiment](2026-08-18-sdxl-cpu-offload.md), which measured it while investigating something else.

## Notes

**Two bugs surfaced only because of this work.**

`sound-effects` and `machine-learning` both ran inference synchronously inside an `async def` — the event-loop-blocking bug already found and fixed in `image-generation` and documented in DESIGN.md sec 3.7. It went unnoticed because nothing polled either service *during* a request until the idle reaper needed to run alongside one. Both now use `asyncio.to_thread`.

`machine-learning` was missing `python-multipart` from its dependencies while using `Form`/`UploadFile`. FastAPI raises this at **import** time, not request time, so that service had never once started successfully from a clean build. It was patched at the image level first (`docker commit`) and the patch was silently discarded by the next `--build`, which is how it got caught properly — the fix now lives in `machine-learning/pyproject.toml` where it survives a rebuild.

**Rebuilds are painfully slow and shouldn't be.** All three Dockerfiles `COPY app.py` *before* `RUN pip install`, so editing one line of application code invalidates the multi-GB dependency layer and triggers a full torch reinstall. This is why the refactor was validated by bind-mounting `app.py` into the existing images rather than rebuilding. Fixing the layer order is complicated slightly by `[tool.setuptools] py-modules = ["app"]` — `pip install .` needs `app.py` present to build the wheel — so it wants its own change, not a drive-by.

**A different approach exists, and may partly supersede this one.** The `ACE-Step-1.5` fork in the projects directory solves the same problem without containers: one process that knows its whole model graph and moves pieces around.

*(This section was written from a first pass over the fork and corrected on 2026-08-18 against its actual source; file/line references below were checked, not remembered.)*

- **VRAM tiers** driving real capability limits — max duration, max batch size, which LM sizes are offered at all (`acestep/gpu_config.py:316`, `GPU_TIER_CONFIGS`). Seven buckets, not six: `tier1`–`tier5`, then `tier6a` (16–20GB) and `tier6b` (20–24GB), plus `"unlimited"`. The 6a/6b split is itself the interesting part — a comment records that it exists to fix a 16GB regression, since "16GB GPUs cannot hold all models simultaneously with the same batch sizes as 24GB GPUs". A tier scheme got its own boundaries wrong once and had to subdivide, which is worth knowing before copying the idea. This GPU is tier3 (6–8GB): batch ≤ 2, 480s with LM, 0.6B LM only, and `offload_to_cpu`/`offload_dit_to_cpu`/`quantization`/`compile` all defaulting to `True`.
- **Auto CPU-offload below 20 GB**, on by default (`gpu_config.py:35`, `VRAM_AUTO_OFFLOAD_THRESHOLD_GB = 20.0`), with a comment recording that the old 16 GB threshold made 16 GB cards OOM: "16GB GPUs cannot hold DiT + VAE + text_encoder + LM simultaneously without offloading". Disabled on Apple Silicon, where unified memory makes offload pointless (`is_mps_platform()` gates compile, quantization and offload together).
- **Intra-request offload** — `offload_to_cpu` loads sub-models *one at a time within a single generation*, far finer-grained than an inter-request TTL. `_load_model_context(model_name)` (`core/generation/handler/init_service_offload_context.py`) is a context manager that moves one named sub-model to the device on entry and back to CPU on exit, logging RSS either side and accumulating the cost into `current_offload_cost`. There is no fixed pipeline order: the calls are scattered across the call sites and interleave (`vae` for conditioning → `text_encoder` → `model` (DiT) → `vae` again for decode), and the LM has its own separate `_load_model_context()` in `llm_inference.py`.
- A **VRAM pre-flight check** that predicts a request's cost (`per_batch_gb × batch × duration_factor + safety_margin`) against `get_effective_free_vram_gb()` and returns a structured error dict instead of OOMing — skipped when offload is on, since then nothing is fully resident (`core/generation/handler/generate_music.py:105`). **It sizes activations only, not weights.** Its own docstring is explicit: "Model weights are already resident in GPU memory at this point. We only need to verify there is enough room for the diffusion-pass activations." That assumption does not hold here — the entire point of the TTL above is that a satellite's weights may *not* be loaded when a request arrives — so porting this formula unchanged would under-predict by the whole model size (~7.1GB for SDXL) and pass requests that then OOM on the load. Any port needs a weights term for the not-yet-loaded case.
- `get_effective_free_vram_gb()`, which adds PyTorch's reserved-but-unallocated cache back to `mem_get_info`'s device-level figure, because the allocator can reuse it without going to the OS.
- `mallopt(M_MMAP_THRESHOLD, 128KB)` so CPU-offloaded tensor storage actually returns to the OS instead of sitting in the glibc arena — relevant to the host-RAM `Exited (137)`s above. `libc.mallopt(-3, 131072)` via `ctypes`, applied at import (`core/generation/handler/init_service_memory_basic.py:47`). **Linux-only** — it early-returns on `platform.system() != "Linux"`, so it works inside the containers and is silently a no-op for anyone running a satellite directly on this Windows host.

The structural difference: containers bought isolation at the cost of coordination. No satellite here can see that another needs the card, so this TTL is a *timeout-based approximation* of coordination — "let go eventually, in case someone else wants it." ACE-Step needs no timeout because it knows.

**What actually transfers, and what doesn't.** Checked against the fork's source rather than inferred from its behaviour:

- **Transfers verbatim.** `get_effective_free_vram_gb()` (`gpu_config.py:1181`) — ~15 lines, torch-only, no dependencies: `device_free + max(0, memory_reserved - memory_allocated)`. This is the cheapest real win available and matters *specifically* because of the TTL: right after an unload, a naive `torch.cuda.mem_get_info()` still reads near-zero free, because the caching allocator holds reserved blocks the OS considers taken but which it can reuse without asking. Any check built on `mem_get_info` alone would refuse work that would actually fit. The `mallopt` call transfers verbatim too.
- **Transfers with adaptation.** Per-service CPU offload, and a per-service pre-flight — the latter needing the weights term noted above.
- **Does not transfer.** The tier system and intra-request offload both assume *one process owning the entire model graph and the whole card*. With four independent tenants, a per-service tier is meaningless: each would size itself against a card three others are also claiming. Sec 3.3's isolation rule forbids the consolidation that would make them work.

So the fork supersedes this TTL *within a single service*, not across the fleet. **Nothing in it addresses the cross-service problem at all** — it never had one. That gap needs an arbiter in `server/` (the only component that calls every satellite), not another borrowed mechanism.

Worth trying next, in order: **CPU offload on the SDXL pipeline** (`image-generation/app.py:147` currently does `_pipe = _pipe.to(DEVICE)`), then a **VRAM pre-flight check** on top of the measured result. Two cautions on the offload step, both found by reading the fork rather than assuming:

- **ACE-Step does not use `enable_model_cpu_offload()`, or any diffusers offload helper** — neither it nor `enable_sequential_cpu_offload` appears anywhere in `acestep/`. It isn't a diffusers pipeline at all; its offload is hand-rolled `_recursive_to_device()` in a context manager. Reaching for the diffusers one-liner is the right *first* move here, since SDXL genuinely is a diffusers pipeline — but it is the analogous idea, not the same code, and it inherits none of the fork's validation.
- Consequently, **"~7.1 GB → ~2 GB" is an estimate with nothing behind it.** Measure before planning around it. Also note `image-generation/app.py:170` builds img2img via `StableDiffusionXLImg2ImgPipeline.from_pipe()`, which *shares components* with the base pipeline, so offload hooks land on both paths — img2img needs testing explicitly, not assuming.
