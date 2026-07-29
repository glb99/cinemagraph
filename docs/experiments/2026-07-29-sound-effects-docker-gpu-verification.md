# Verifying sound-effects/ against a real backend: Docker + GPU + the web UI

**Date:** 2026-07-29
**Question:** `sound-effects/README.md` already documented Python-level validation against a real
GPU, but explicitly flagged the Docker build itself as unvalidated (Docker wasn't running in that
earlier session). The web UI's Sound effects tab was also wired but never exercised against a live
backend. Does the whole chain -- Docker Compose, a real GPU, the actual API, the actual UI -- work
end to end?

## What was tried

Confirmed a real GPU and Docker GPU support were actually available before assuming anything:
`nvidia-smi` showed an RTX 4060 (8GB VRAM), and `docker info` listed the `nvidia` runtime.

Uncommented `SOUND_EFFECTS_URL=http://sound-effects:8003` in `docker-compose.yml`'s `core` service
(previously commented out by convention until actually running that service) and ran
`docker compose --profile audio build core sound-effects`.

## Result: three real, sequential blockers, each found by actually running it

### 1. Gated model, no token

First start failed immediately:

```
huggingface_hub.errors.GatedRepoError: 401 Client Error.
Cannot access gated repo for url .../stabilityai/stable-audio-open-1.0/resolve/main/model_config.json.
```

`stabilityai/stable-audio-open-1.0` requires an authenticated, license-accepted HF token -- not
documented anywhere in this repo before now. Fixed by:
- Wiring `HF_TOKEN=${HF_TOKEN}` into `docker-compose.yml`'s `sound-effects` service (Compose
  variable substitution from a local `.env` file).
- Adding `.env` to `.gitignore` -- Compose auto-loads it for `${VAR}` substitution, and it must
  never be committed.
- The user accepted the model's license on its HF page and created a token, dropped directly into
  `.env` (never pasted into chat).

### 2. No GPU passthrough

`sound-effects`'s compose entry never actually had a `deploy: resources: reservations: devices:`
block, despite its own comment inviting one ("add a `deploy:` block matching acestep's above if you
want to pass one through") -- CPU-capable per the Dockerfile, but noticeably slow for a diffusion
model. Added the same block the commented-out `acestep` entry already has (`driver: nvidia`,
`count: all`, `capabilities: [gpu]`). Confirmed via `GET /health` afterward: `{"device": "cuda"}`.

### 3. `torchaudio.save` needs a backend the image doesn't have

With the token and GPU fixed, a real `POST /generate/sound-effect` request ran the full diffusion
sampling successfully (confirmed via `sound-effects` container logs reaching the save step), then
failed:

```
ImportError: TorchCodec is required for save_with_torchcodec. Please install torchcodec to use this function.
```

A recent `torchaudio` release delegates WAV encoding to an optional `torchcodec` backend rather than
bundling one. First attempt: added `torchcodec>=0.1.0` to `sound-effects/pyproject.toml`. That
surfaced a second failure one layer down:

```
OSError: Could not load this library: .../torchcodec/libtorchcodec_core5.so
OSError: libavutil.so.56: cannot open shared object file: No such file or directory
```

`torchcodec` itself needs real system-level FFmpeg shared libraries, which the `python:3.11-slim`
base image doesn't have -- chasing this further would mean adding apt packages and matching FFmpeg
library versions to whatever `torchcodec` was built against, a genuine dependency-whack-a-mole risk
for a wrapper the actual generation code doesn't need. Backed out `torchcodec` and instead rewrote
the save step in `sound-effects/app.py` to use Python's stdlib `wave` module directly -- WAV is a
simple enough format not to need a library at all:

```python
pcm = (output.numpy() * 32767.0).astype(np.int16)  # (channels, samples)
buf = io.BytesIO()
with wave.open(buf, "wb") as wf:
    wf.setnchannels(pcm.shape[0])
    wf.setsampwidth(2)
    wf.setframerate(sample_rate)
    wf.writeframes(pcm.T.tobytes())
```

Also tried declaring `numpy` explicitly in `pyproject.toml` (since `app.py` now imports it directly)
-- that produced a third, unrelated failure: a real `pip` `ResolutionImpossible`, since
`stable-audio-tools`'s own dependency chain (`laion-clap`) pins an exact `numpy==1.23.5`, and a second,
looser constraint on top was unsatisfiable. Removed the explicit pin; `numpy` was already guaranteed
present transitively via `torch`/`stable-audio-tools`, so nothing was actually missing.

## Verified, not just fixed

After all three fixes, ran the exact request that had failed twice before
(`"gentle wind chimes in a light breeze"`, 5s) and confirmed a real result, not just a 200:

- Downloaded the actual output file and parsed it with the stdlib `wave` module: stereo, 44.1kHz,
  16-bit, exactly 5.0s.
- Checked the raw PCM samples: full dynamic range (-31656 to 32767) and 95% non-zero samples --
  genuinely generated audio, not silence or garbage.

Then repeated the check through the actual web UI, not just curl: clicked the Sound effects tab
(now visible, confirming `GET /capabilities` correctly reports `sound_effect_generation: true`),
submitted a real prompt through the real form (`"crackling campfire with distant crickets"`),
and after the job completed, read the resulting `<audio>` element's own properties directly --
`{"duration": 5, "readyState": 4, "networkState": 1}`. `readyState: 4` is `HAVE_ENOUGH_DATA`, the
same rigor used earlier in this project for verifying video playback, not just "the API returned
bytes." Also confirmed both generated assets appeared correctly in the Library tab, tagged
`["sound-effect"]` with `provenance: {prompt, duration}` matching what was actually requested.

## Verdict

- [x] Adopted -- `HF_TOKEN` wiring, GPU passthrough, and the `wave`-based WAV write are all kept.
      `docs/DESIGN.md`'s decision log has the corresponding entries.
- [x] `sound-effects/README.md` updated: the gated-model prerequisite is now documented, and the
      "Docker build not yet validated" caveat is resolved.

## Notes

Session discipline: every `docker compose` invocation in this investigation (multiple rebuild/retry
cycles) was explicitly torn down afterward (`docker compose --profile audio down`, plus verifying no
orphaned host-side `docker.exe` processes) before moving on -- `docker compose down` alone does not
remove containers started under a non-default profile like `audio`; the same `--profile audio` flag
is needed on `down` too.
