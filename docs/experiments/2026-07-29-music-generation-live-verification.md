# Music generation: verifying `/generate/music` against a live ACE-Step server

**Date:** 2026-07-29
**Question:** `docs/DESIGN.md` §5.7 marked ACE-Step integration as code-complete but explicitly
"not yet validated against a live server -- no GPU/running instance was available to test
against." Does the whole chain (`POST /generate/music` -> `_run_music_job`'s poll loop -> real
audio -> library -> UI) actually work end to end against a real instance?

## Setup: running ACE-Step

This machine already has a local `ACE-Step-1.5` checkout (separate from `cinemagraph-tool`) with
its own `uv` environment and checkpoints already downloaded (`acestep-v15-turbo`,
`acestep-5Hz-lm-1.7B`, VAE, Qwen3-Embedding-0.6B) -- no fresh install needed. `docker-compose.yml`'s
commented-out `acestep` entry assumes running ACE-Step's published `ghcr.io/ace-step/ace-step-1.5`
image as a second containerized service instead; that would mean re-downloading everything the
local checkout already has, purely for architectural consistency with `sound-effects/`/
`image-generation/`. Not worth it here -- ACE-Step was started natively on the host instead
(`uv run acestep-api`, port 8001), and `core`'s `ACESTEP_URL` was pointed at
`http://host.docker.internal:8001` (Docker Desktop's standard host route) rather than a container
DNS name. `docker-compose.yml` keeps both options visible: the active `host.docker.internal` line
plus a commented alternative for the containerized route, should a future machine not have a local
ACE-Step checkout to point at.

## GPU tier note

This machine's RTX 4060 (8GB) falls into ACE-Step's own Tier 3 (6-8GB): only the 0.6B LM is
supported, not the 1.7B checkpoint that happened to be the one already downloaded locally. Not a
problem in practice -- ACE-Step auto-selects per its own GPU-tier detection and downloaded the 0.6B
LM automatically on first real request rather than trying to force the 1.7B checkpoint. Confirmed
via `/health` before vs. after the first request (`llm_initialized: false` -> a working
`acestep-5Hz-lm-0.6B` in the generation response's `lm_model` field).

## Verification steps

1. **Direct against ACE-Step, bypassing `cinemagraph-tool` entirely** -- `POST /release_task` with
   a simple prompt (`thinking: false`, `audio_duration: 15`), polled `POST /query_result` until
   `status: 1` (5 polls, ~20s), downloaded via `GET /v1/audio?path=...`. Result: a real 240813-byte
   MP3, confirmed via `file` as valid `MPEG ADTS, layer III, v1, 128 kbps, 48 kHz, Stereo` --
   240813 bytes matches 15s at 128kbps almost exactly, not a truncated/corrupt file.

   Noted in passing: even with `thinking: false` requested, the response's `lm_model` field showed
   `acestep-5Hz-lm-0.6B` was used and `generation_info` included an "LM phase" -- not a bug, this is
   ACE-Step's documented metadata auto-completion (`bpm`/`key_scale`/`time_signature`/
   `audio_duration` get filled by a quick LM call whenever left unset, independent of `thinking`,
   which only gates *audio-code* generation). `_run_music_job` doesn't set those fields itself, so
   every real request will trigger this regardless of the UI's "Thinking mode" checkbox -- expected,
   not something to fix.

2. **Through `core`'s own `/generate/music`** -- `docker compose up -d core` (image already built
   from earlier work), confirmed `GET /capabilities` flipped `music_generation` to `true` purely
   from the env var + a live health check (no code path specific to this test), submitted a real
   form-encoded request, polled `GET /jobs/{id}` to `done` (2 polls, ~10s -- ACE-Step's model was
   already warm from step 1), downloaded via `GET /jobs/{id}/file`: identical byte-for-byte shape to
   step 1 (240813 bytes, valid MP3). Confirmed the asset landed in `GET /library` tagged
   `["music"]` with `provenance` matching exactly what was requested (`prompt`/`lyrics`/`duration`/
   `thinking`).

3. **Through the real web UI** -- loaded `http://localhost:8000`, clicked the Music tab (visible,
   confirming capability-gating works end to end from a real page load, not just a raw
   `/capabilities` call), filled the actual form fields (prompt, duration, toggled the thinking
   checkbox), clicked Generate, and let the page's own `pollJob()` drive it -- watched the on-page
   status text go `running` -> `done`. Read the resulting `<audio id="music-preview">` element's own
   properties directly via injected JS rather than trusting the status text: `readyState: 4`
   (`HAVE_ENOUGH_DATA`), `duration: 15` (matches the requested duration), `error: null`, `display:
   block` -- the same rigor used earlier for video/image (`complete`/`naturalWidth` checks), not
   just "the API returned 200." Switched to the Library tab and confirmed both music assets
   (the direct-API test and the real-UI test) show up correctly tagged.

## Cleanup

`docker compose down` (removed `core`), plus stopped the host-side `uv run acestep-api` process
directly (`TaskStop` on its background task) -- both torn down before finishing, per this project's
standing rule of never leaving a `docker compose`-adjacent process running unattended. Also found
and removed an unrelated leftover `cinemagraph-tool-image-generation-1` container (26 minutes old,
`Up`) from an earlier session's work that hadn't been torn down -- caught via `docker ps -a` before
starting this task, not left for later.

## Follow-up: containerizing it (same day)

The host-native setup above worked, but left ACE-Step outside `docker compose` entirely, unlike
`sound-effects/`/`image-generation/`. Asked to bring it in line with those two: uncommented the
`acestep:` service (ACE-Step's own published image, no wrapper needed) and, to avoid re-downloading
the ~11GB of weights the host checkout already had, bind-mounted that checkout's `checkpoints/`
directory straight into the container (an absolute Windows host path) instead of letting the
container fetch its own copy.

**Found a real bug doing this: it hung permanently, not just slowly.** First request after startup
made it through `POST /release_task` and started loading the main DiT model (visible in logs,
~9s), then everything stopped -- no further log lines, and within the same couple of minutes even
`GET /health` stopped responding at all (it had been responding fine seconds earlier). `docker
stats` showed near-zero GPU utilization and only ~14% CPU the whole time -- not a compute stall.
Diagnosed with `docker exec ... ps aux` (the worker process was in Linux `D` state, uninterruptible
disk-sleep) and `cat /proc/<pid>/wchan` (returned `p9_client_rpc`): the process was blocked inside
Docker Desktop's WSL2 cross-OS file-sharing protocol (9P), reading from the bind-mounted Windows
directory. Waited a further 60s with no progress and the container's own internal healthcheck curls
piling up unanswered in `ps aux` -- genuinely hung, not just slow. Confirmed no host-side lock
contention first (`Get-Process | Where-Object {$_.ProcessName -match 'python|uv'}` showed nothing
running on the host after the earlier native-mode process had been stopped).

Root cause, best understanding: whatever I/O pattern ACE-Step's model loader uses (almost certainly
`mmap`-based safetensors loading) doesn't work reliably over Docker Desktop's WSL2 9P bridge when
reading a large, pre-existing checkpoint tree from an arbitrary Windows path. `sound-effects/` and
`image-generation/` never hit this because their models are *downloaded by the container itself*
into a project-relative bind mount (`./data/*-cache`) rather than bind-mounted in fully-formed from
an unrelated project -- the same style of mount, but populated by the container's own sequential
HTTP download rather than pre-existing on the host.

**Fix:** dropped the absolute-path mount entirely; `acestep`'s `checkpoints`/`hf-cache` volumes are
now `./data/acestep-checkpoints` and `./data/acestep-hf-cache`, matching the sibling services
exactly -- empty on first run, populated by the container's own download. Re-ran the full thing:
`docker compose --profile audio up -d acestep` (`ACESTEP_INIT_SERVICE=false`, lazy load), then a
direct `POST /release_task` to trigger the download -- ~11GB across 28 files (main DiT 4.79GB, LM
1.7B 3.71GB even though only 0.6B is actually used at generation time -- ACE-Step appears to fetch
more of the model family than it ends up loading, not investigated further since it isn't wrong,
just a bit wasteful; Qwen3 embedding 1.19GB, VAE 337MB, plus a separate ~1.33GB fetch for the
tier-appropriate 0.6B LM once generation actually started), all downloading and completing normally
with steady progress in the logs -- no repeat of the hang. Generation then ran and finished
correctly (real MP3, byte-identical shape to every earlier test). Re-verified the full chain again
afterward exactly as in steps 2 and 3 above, this time against the container instead of the host
process: `core`'s `/generate/music` -> done in ~45s with a warm model, correct library registration,
and the real UI's Music tab producing an `<audio>` element with `readyState: 4`, `duration: 15`,
`error: null`.

Cleanup for this pass needed an extra step beyond the usual `docker compose down`: `down` without
`--profile audio` only removed `core`, leaving `acestep` running -- the exact `--profile` scoping
gotcha this project already hit once before with `sound-effects` (see the Docker workflow debugging
notes earlier in this project's history). `docker compose --profile audio down` cleared it properly;
confirmed via `docker ps -a` afterward.

## Follow-up: `instrumental` support, and the WSL2 hang recurring (2026-07-30)

Asked to add proper support for instrumental (no-vocals) generation. Checked ACE-Step's own REST
API docs (`api/API.md`) first -- no `instrumental` boolean field documented there at all (that only
exists on ACE-Step's separate, higher-level OpenRouter-compatible wrapper, a different API surface
this project doesn't call). Confirmed the real mechanism by reading ACE-Step's actual source
(`acestep/api/server_utils.py`'s `is_instrumental(lyrics)`): the real `/release_task` endpoint
derives instrumental mode purely from the *lyrics string itself* being exactly `"[inst]"` or
`"[instrumental]"` (case/whitespace-insensitive) -- an empty `lyrics` field does **not** trigger it,
so there was previously no reliable way to request an instrumental track through this project's own
UI/API at all.

Added `instrumental: bool` end to end (`ui.py`'s Music tab checkbox -> `/generate/music`'s form
field -> `run_music_job`'s parameter), which overrides whatever `lyrics` text was submitted with
ACE-Step's real `"[Instrumental]"` marker before sending it, and records both the effective lyrics
and the `instrumental` flag in the library's provenance. Unit-tested
(`test_music_job_instrumental_overrides_lyrics`: asserts the exact marker string reaches
`/release_task`'s JSON body regardless of what lyrics text was typed) -- 34/34 tests passing.

**Verifying it against a live container surfaced that the WSL2 hang from the pass above was only
half-fixed.** Restarted `acestep`+`core` fresh (containers had been recreated after an unrelated
Docker Desktop restart) and submitted a real `instrumental=true` request through `core`. It failed
with the same generic "unreachable" error. First hypothesis: `call_optional_service`'s default 30s
timeout wasn't enough margin for a cold model load blocking a single `/query_result` call
synchronously (a real, separate, smaller issue -- fixed by giving that specific poll call a 90s
timeout instead of the default 30s). But after fixing that and rebuilding `core`, the *exact same*
failure recurred, and `docker exec ... ps aux` + `cat /proc/<pid>/wchan` showed the identical
`p9_client_rpc` disk-sleep hang from the previous day -- on `./data/acestep-checkpoints`, the
project-relative bind mount that was supposed to have fixed this.

**Revised understanding:** the trigger was never "pre-populated by an external project" vs.
"downloaded by this container" -- both are Windows-path bind mounts under Docker Desktop, and the
earlier fix only *appeared* to work because that verification read the checkpoint files
immediately after the container itself had just written them (still hot in the container's page
cache, no fresh 9P round-trip needed). A cold container restart reading those same,
now-pre-existing files back from *any* Windows bind mount hits the same hang, regardless of which
project wrote them or what path they live under. `sound-effects/`/`image-generation/` haven't
necessarily proven immune to this -- they may simply not have been restart-tested this way in this
project's history yet.

**Actual fix:** switched `acestep`'s `checkpoints`/`hf-cache` volumes from bind mounts to named
Docker volumes (`acestep-checkpoints`/`acestep-hf-cache`), which live natively inside the Docker
Desktop WSL2 VM's own Linux filesystem and never cross the Windows/9P bridge at all -- structurally
can't hit this class of hang. Confirmed via `ps aux` after a fresh restart with the new config: the
worker process was in `Sl` (normal, interruptible sleep) rather than `D` the whole time, actively
consuming CPU -- a real, healthy download, not a hang.

The fresh named-volume download stalled to kB/s-range network speeds partway through (unrelated to
Docker/WSL2 -- an external network/CDN condition on the day), so this pass was paused without a
completed generation to check by ear -- the named-volume fix itself rested only on process-state
evidence (`Sl` not `D`) at that point, not a finished happy path.

**Resolved the same day, once the download finished in the background.** The queued task from the
stalled attempt kept running server-side the whole time -- ACE-Step's own job queue doesn't care
whether the HTTP client that submitted it is still waiting -- and had actually finished generating
by the time this was revisited (found via the container's own logs showing two completed
`AudioSaver` writes). Pulled both files out directly (`docker cp`) and sent them to the user to
listen to by ear -- the actual, only-reliable test of whether "no vocals" really happened, not just
that the request shape was correct. Then, with the model now warm, ran one more clean request
straight through `core`'s real route end to end: `POST /generate/music` (`instrumental=true`, with
deliberately-included lyrics text designed to prove it gets ignored) -> done in ~4 polls with no
error -> downloaded via `GET /jobs/{id}/file` (real MP3, correct duration) -> library provenance
confirmed `"lyrics": "[Instrumental]", "instrumental": true` -- the override reached ACE-Step and
was recorded correctly, through the actual application path a user would take, not a bypassed
internal check.

## Verdict

- [x] Validated -- `/generate/music` works end to end against a real ACE-Step instance, through the
  API directly, through `core`'s proxy, and through the real web UI, with output confirmed as
  genuine playable audio at every layer, not just a non-error HTTP status. Verified against ACE-Step
  running natively on the host, and against it containerized (both the bind-mount and, finally, the
  named-volume config).
- [x] Adopted -- ACE-Step as a `docker compose --profile audio` service (`acestep:`, the published
  `ghcr.io/ace-step/ace-step-1.5` image), checkpoints in named Docker volumes
  (`acestep-checkpoints`/`acestep-hf-cache`), not bind-mounted -- two different bind-mount attempts
  (an absolute external path, then a project-relative one) both hit the same WSL2 `p9_client_rpc`
  disk-sleep hang on a cold restart; named volumes structurally avoid it, confirmed by an actual
  successful generation afterward, not just process-state evidence.
- [x] Added and confirmed -- `instrumental` support (UI checkbox -> form field -> `run_music_job`
  parameter) overrides `lyrics` with ACE-Step's real `"[Instrumental]"` marker (not documented in
  its own REST API docs -- found by reading its source). Unit-tested, and verified against a real
  generation both directly (files sent to the user to confirm by ear) and through `core`'s full
  route with lyrics text deliberately included to prove it gets overridden, not just left empty.

This closes out `docs/DESIGN.md` §5.7's last open item -- Photo, Video, Library, Music, Sound
effects, and Image are now all verified against live servers, and Music now matches the other two
optional services' shape (a `docker compose` service, not a special case). The named-volume finding
is also a flag worth checking against `sound-effects/`/`image-generation/` at some point -- this
project has no confirmed evidence either way for those two under a cold restart with pre-existing
cached weights (see the spawned follow-up task for this).
