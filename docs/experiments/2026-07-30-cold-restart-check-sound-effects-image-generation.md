# Cold-restart check: do sound-effects/image-generation share ACE-Step's WSL2 hang?

**Date:** 2026-07-30
**Question:** ACE-Step's `acestep` container hung permanently on a cold restart reading pre-existing
model checkpoints from a Windows-path bind mount (Docker Desktop's WSL2 file-sharing bridge,
confirmed via `/proc/<pid>/wchan` -> `p9_client_rpc`; see
`docs/experiments/2026-07-29-music-generation-live-verification.md`). `sound-effects/` and
`image-generation/` use the same bind-mount style (`./data/*-cache`) for their own downloaded model
weights, and had never specifically been restart-tested this way. Are they vulnerable to the same
hang?

## image-generation/ -- tested, no hang

`./data/image-generation-cache` was already populated (14GB) from earlier work. Started the
container fresh (`docker compose up -d image-generation`) and watched both logs and process state
throughout:

- Weight loading (`Loading weights: 100%|##########| 196/196`) completed in ~5 seconds --
  `/proc/1/status` showed a brief `D` (disk sleep) during the read, immediately followed by `R`
  (running), not a stall.
- Moving the pipeline to CUDA took longer (a couple of minutes total before `/health` responded --
  expected, since `app.py`'s own `@app.on_event("startup")` blocks Uvicorn from accepting
  connections until `_get_pipe()` returns, the same eager-load-before-serving pattern this project
  just added to ACE-Step). `/proc/1/status` during this window showed `VmRSS` growing steadily
  (~8.7GB, consistent with the full fp16 SDXL pipeline) and state cycling normally between `R`/`S`
  -- never `D` for longer than a normal disk read, and `wchan` never showed `p9_client_rpc`.
- `GET /health` eventually returned `{"status": "ok", "device": "cuda"}`, and a real
  `POST /generate` request (512x512, 8 steps) returned a valid 512x512 PNG in well under a minute.

**Conclusion: no hang.** Reading this container's own pre-existing, self-downloaded cache back from
its bind mount on a cold restart works fine.

## sound-effects/ -- blocked by an unrelated auth problem, not tested

`./data/sound-effects-cache` didn't exist on this machine's current `./data` state (never
downloaded here), so a cold-restart-with-pre-existing-files test wasn't directly possible --
starting the container fresh to seed the cache hit `stabilityai/stable-audio-open-1.0`'s gated-repo
check instead:

```
huggingface_hub.errors.GatedRepoError: 401 Client Error ... Cannot access gated repo for url
https://huggingface.co/stabilityai/stable-audio-open-1.0/resolve/main/model_config.json.
```

The `.env`-provided `HF_TOKEN` no longer has access to this gated model (expired, revoked, or the
account's license acceptance lapsed) -- an account/credential issue, not something fixable from
this session. Stopped and removed the container; no further sound-effects testing was possible this
pass.

## Verdict

- [x] `image-generation/` -- confirmed immune to the WSL2 cold-restart hang. The earlier finding
  (bind-mounting pre-existing checkpoint files from *any* Windows path can hang, not just an
  external absolute path) does not appear to generalize to every service using this style of mount
  -- it may be specific to something about ACE-Step's own loading code (its `mmap` pattern,
  multi-file checkpoint structure, or something else not yet isolated), not a blanket Docker
  Desktop/WSL2 limitation that always bites a large bind-mounted model cache.
- [ ] `sound-effects/` -- not tested; blocked by an expired/invalid `HF_TOKEN` needing a fresh
  Hugging Face token with accepted access to `stabilityai/stable-audio-open-1.0`, which only the
  project owner can resolve (regenerate at
  [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens), confirm the model's
  license is still accepted on its Hub page).

Given `image-generation/` came back clean, the ACE-Step hang looks more likely to be
ACE-Step-specific than a general "any bind-mounted model cache hangs on this machine" risk --
tempering the broader concern raised in the earlier writeup. Still worth eventually re-testing
`sound-effects/` once the token is refreshed, since it's a different model-loading path
(`diffusers`' `StableAudioPipeline` vs. ACE-Step's own custom loader) and hasn't been ruled out
either way.

Cleanup: `docker compose down` doesn't remove profile-gated containers unless the same `--profile`
flag is passed (hit this again with `image-generation`, `profile: ["image"]` -- the same gotcha
documented before with `sound-effects`). Explicit `docker rm` cleared the leftover; confirmed via
`docker ps -a`.
