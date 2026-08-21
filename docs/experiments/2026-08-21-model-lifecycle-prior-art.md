# How other open-source projects handle stuck / resource-hogging local models

**Date:** 2026-08-21
**Question:** we already unload on an idle TTL (see 2026-08-18-lazy-model-ttl-and-hf-cache-volumes.md).
That covers *idle*. It does not cover *stuck*, and it does not cover *contending*. How do comparable
open-source projects handle those two, and which of their mechanisms are worth adopting here?

## What was tried

Desk research across projects that run heavy local models behind an HTTP API on one machine:
LocalAI, Ollama, llama-swap, ComfyUI, Immich (whose TTL semantics this project already copied),
NVIDIA Triton, plus PyTorch's own issue tracker for what is and isn't recoverable in-process.
No code was changed. Findings were then checked against this repo's three satellites.

## Result

### The prior art splits into four distinct mechanisms, and we have implemented one

| # | Mechanism | Solves | Who does it | Here? |
|---|---|---|---|---|
| 1 | **Idle TTL / keep-alive** — unload after N seconds unused | idle memory | Immich (`MACHINE_LEARNING_MODEL_TTL`, 300s), Ollama (`OLLAMA_KEEP_ALIVE`), LocalAI idle watchdog, llama-swap `ttl`, LM Studio | **yes**, `MODEL_TTL=900` |
| 2 | **Busy watchdog** — kill a model that has been *processing* too long | **stuck** | LocalAI (`LOCALAI_WATCHDOG_BUSY`) | **no** |
| 3 | **Admission control** — cap how many models may be resident, evict to fit | contention | LocalAI (`MAX_ACTIVE_BACKENDS`, LRU), Ollama's scheduler, llama-swap groups, Triton rate limiter | **no** (we approximate it with TTL) |
| 4 | **Process-level isolation** — each model in a killable subprocess | stuck *and* contention | LocalAI (gRPC backends), llama-swap, Ollama (per-model runner), Triton | **no** (in-process globals) |

The gap that matters most is #2, and #4 is what makes #2 actually work.

### LocalAI is the closest match to our problem and the most directly useful

LocalAI runs several heavy, mutually-exclusive backends on one GPU behind one API — structurally
the same situation as our three satellites. It ships *two* watchdogs, not one:

```bash
LOCALAI_WATCHDOG_IDLE=true  LOCALAI_WATCHDOG_IDLE_TIMEOUT=15m \
LOCALAI_WATCHDOG_BUSY=true  LOCALAI_WATCHDOG_BUSY_TIMEOUT=5m \
LOCALAI_MAX_ACTIVE_BACKENDS=3
```

The busy watchdog terminates a backend that has been mid-request past a threshold (for parallel
backends it measures from the *oldest in-flight* request). That is precisely the failure we don't
handle. It also has `LOCALAI_FORCE_BACKEND_SHUTDOWN=true`, which escalates a graceful shutdown that
timed out (30s default) into outright process termination — an admission, in their own docs, that
asking nicely sometimes doesn't work.

Worth noting honestly: LocalAI's issue tracker shows this is hard even for them. #2277 (watchdog
doesn't kill an idle sentencetransformer backend), #1760 (killing an idle llama backend crashes with
an invalid pointer), and, most relevant to us, cases where "the watchdog logs show it successfully
kills the process ... but the python process remains stuck and loaded in GPU memory." So the
mechanism is right, but a Python-level kill is not guaranteed to return VRAM.

### Why an in-process reaper structurally cannot fix a stuck model — and why ours is worse than it looks

PyTorch's own tracker is clear that a hung CUDA call is not recoverable from inside the process: CUDA
ops can hang under VRAM pressure without ever raising into Python (pytorch#178491), and full memory
release "may only be possible by shutting down the current process" — `torch.cuda.empty_cache()` is
useless once the interpreter is wedged. The accepted remedy for containers is to restart the
container; on bare metal, `fuser -k -9 /dev/nvidia*`.

Checked against our code, this is not theoretical. All three satellites shared one shape
(`machine-learning/app.py`, `image-generation/app.py`, `sound-effects/app.py`) — described here as
it was *before* the fix below:

- the request handler held `_inference_lock` for the whole call, and
- `_reap_idle_model()` also did `async with _inference_lock` before unloading.

So if `_run_segment` / the pipeline call hangs inside `asyncio.to_thread`, `_last_used` stops
advancing, the reaper wakes up, correctly decides the model is past TTL, and then **blocks forever on
the lock the stuck request still holds**. The idle TTL is defeated by exactly the case we most need
it for. The model stays resident, the GPU stays occupied, and the other two satellites can never get
the card.

Two further consequences fall out of the same check:

- **`/health` does not take the lock**, so a wedged satellite keeps returning
  `{"status": "ok", "model_loaded": true}`. Core's `service_available()` therefore reports it as
  available, `GET /capabilities` says `true`, and the UI keeps offering a capability that can no
  longer complete a request. A liveness probe that only proves the event loop is alive cannot detect
  this class of failure — that's the "beyond 'the process is alive'" point in the Docker healthcheck
  literature.
- **Client timeouts don't cancel server work.** Core gives up per `call_optional_service`'s timeout
  (30s default; 90/120/300s at various adapter call sites) and returns a clean 503 — but the satellite
  never learns, keeps computing, keeps the lock, keeps the VRAM. A user retry then queues *behind*
  the abandoned work. Our 503 means "I stopped waiting," not "it stopped running."

### Docker restart policies won't save this either

`restart: unless-stopped` reacts only to the process *exiting*. An unhealthy-but-alive container is
never restarted by Docker itself; that needs a sidecar (`willfarrell/docker-autoheal`) or an
orchestrator. Our compose file currently declares neither `healthcheck:` nor `restart:` on any
service, so today nothing at all recovers a wedged satellite except a human running
`docker compose restart`.

### The other approaches, and how well they'd transfer

- **Ollama's scheduler** does what our TTL approximates, but properly: it estimates a model's memory
  need up front (weights + KV cache + graph), tracks per-GPU allocation, and evicts "the smallest
  model that lets the new model fit." That's admission control with real numbers instead of a timer.
  Transferring it fully would mean a VRAM-aware scheduler in core — a big build, and our satellites
  are so nearly mutually exclusive on an 8GB card (SDXL alone ~7.1GB) that the interesting case is
  just "one at a time," which is far cheaper to enforce.
- **llama-swap** is the interesting off-the-shelf shape: a small Go proxy that keeps one model
  running, stops another when VRAM is needed, applies a per-model TTL, and — crucially — verifies
  upstream readiness by polling a `checkEndpoint` before proxying. Because each model is a
  *subprocess it owns*, "unload" is a process kill and always works. Same idea as our
  `_external_service.py` seam, but with lifecycle authority over the thing it proxies to, which is
  the part we don't have.
- **ComfyUI** solves the third problem (a single model too big for the card) rather than ours: VRAM
  modes (`--highvram`/`--normalvram`/`--lowvram`/`--novram`), a reserved headroom buffer (~400MB,
  600MB on Windows) plus a ~0.8GB working buffer, and dynamic partial offload that gives weights back
  the moment another application wants the memory. Our 2026-08-18 SDXL CPU-offload work is already
  the same family of fix; the reserve-headroom idea is the transferable bit we haven't copied.
- **Triton** offers `--model-control-mode=explicit` (nothing loads unless asked, load/unload is an
  API call, not a timer) and a rate limiter where instances must reserve a declared GPU budget before
  running. Explicit control is a genuinely different philosophy from TTL and worth remembering: it
  moves the decision to the caller — which, for us, would be core, the only component that knows a
  music job and an image job are about to contend.

## Verdict

- [x] Adopted (items 1–3), on branch `fix/satellite-busy-watchdog`. Item 4 deliberately not built.

They were written up as three items but shipped as one change, because they are one mechanism:
a supervisor that can tell idle from busy from wedged. Any two of the three without the
third recover nothing on their own.

1. **The idle reaper no longer blocks on the inference lock.** It acquires with
   `asyncio.wait_for(..., timeout=5)` and skips the tick on failure, and it skips outright while
   a request is genuinely in flight. This is what fixes the deadlock above.
2. **`/health` reports in-flight state** — `inflight_seconds`, plus `status: "stuck"` and a 503
   past the threshold. No core change was needed: `service_available()` already keys on
   `status_code == 200`, so a wedged satellite drops out of `/capabilities` on its own.
3. **`BUSY_TIMEOUT` (default 1200s, 0 disables) kills the process** via `os._exit(1)`, paired
   with `restart: unless-stopped` on all three satellites in compose. The pairing is the whole
   mechanism — exiting without a restart policy converts a stuck service into a missing one.

Deliberately **not** done:

4. **Cross-satellite admission control.** Core is the natural place for it (the only component
   that sees all jobs), and "one GPU job at a time" would capture most of Ollama's scheduler for
   a fraction of the work. Left undone because with 1–3 in place a stuck model self-heals in
   ~20 minutes instead of never, which removes the urgency. Revisit if two satellites are still
   observed fighting over the card after this has run for a while.
5. **Subprocess-per-model** (the llama-swap/LocalAI shape). Architecturally the better answer,
   but container-level isolation already provides the kill boundary; `os._exit` + a restart
   policy buys the same recovery semantics for ~30 lines per service instead of a rewrite.

### Threshold choice, and what's still unmeasured

`BUSY_TIMEOUT=1200` is set well above the known worst case rather than tuned to a measured one.
The binding number is image-generation's cold load (~6 min through the old WSL2 bind mount);
that cache is a named volume now and the real figure should be far lower, but it has not been
re-measured. Killing a slow-but-healthy generation is a worse failure than waiting out a
genuinely stuck one, so the default stays generous until that measurement exists — the same
measurement that would let `MODEL_TTL` drop from 900s back toward Immich's 300s.

### Not verified end-to-end

This ships syntax-checked and reviewed, not exercised: reproducing a wedged CUDA call needs the
GPU host, and the satellites have no test harness (their deps are torch-class and deliberately
outside core's venv). The unfalsified assumption is that a hung call actually leaves
`_inflight_since` set and the event loop responsive — true for a blocked `to_thread` worker,
which is the observed failure, but not for a hang that takes the event loop down with it. The
cheap real-world check is to set `BUSY_TIMEOUT=30` on one satellite, start a normal generation,
and confirm the process exits with the FATAL line and the container comes back.

Note that 1–3 are per-satellite and must be written out three times, not shared — docs/DESIGN.md
sec 3.3 forbids these services sharing code with each other, the same rule the existing TTL logic
already follows.

## Notes

- Immich's default TTL is 300s; ours is 900s for a measured reason (cold SDXL load was ~6 min through
  the WSL2 bind mount). Now that the HF caches are named volumes, re-measuring cold load and dropping
  back toward 300 is a separate, already-identified follow-up — see the compose comment.
- Ollama's `OLLAMA_MAX_LOADED_MODELS=1` is the whole idea of item 4 in one env var, for the
  single-process case. Our equivalent is harder only because our models live in three containers.
- LocalAI's `--force-eviction-when-busy` carries an explicit "this can interrupt active requests"
  warning. Worth copying that honesty: any busy watchdog we add will occasionally kill a slow-but-fine
  job, so the threshold has to sit above our real worst case (ACE-Step's own LM step alone is budgeted
  at 480s on this GPU tier — see 2026-08-01-acestep-poll-timeout.md).

Sources: LocalAI VRAM management docs and issues #1760/#2277/#5221; Immich ML docs and PR #3340;
Ollama scheduler (DeepWiki 2.2); llama-swap (mostlygeek/llama-swap); ComfyUI memory optimization;
Triton model management + rate limiter docs; pytorch#178491, #121203, #26094.
