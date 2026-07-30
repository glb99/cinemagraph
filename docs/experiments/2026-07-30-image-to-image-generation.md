# Adding image-to-image (img2img) generation

**Date:** 2026-07-30
**Question:** the project owner wants to pass a reference photo to image generation instead of
(or alongside) a text prompt -- is this possible with the current SDXL setup, and can it be built
without regressing the already-working text-to-image path?

## Feasibility check

Confirmed directly against the installed package rather than assumed (this project's own
established habit after the Gemini SDK episode, where doc summaries turned out to be wrong):

```
diffusers version: 0.39.0
has StableDiffusionXLImg2ImgPipeline: True
has from_pipe: True
```

`StableDiffusionXLImg2ImgPipeline.from_pipe(pipe)` wraps the *same already-loaded*
UNet/VAE/text-encoders in a different pipeline class (`image=`/`strength=` instead of
`width=`/`height=`) -- no second copy of the model loaded from disk.

## Scope decisions (confirmed with the project owner before building)

- Prompt still required; the reference image guides the result, doesn't replace the prompt.
- Extends the existing `POST /generate/image` route (optional `reference_image` upload) rather
  than a separate route.
- `strength` (how far the result may deviate from the reference) is user-adjustable with a
  sensible default (0.6), not fixed.

## What was built

- `image-generation/app.py`: `/generate` switched from a JSON body to multipart/form-data (a
  JSON body can't carry an uploaded file cleanly) -- `prompt`, `negative_prompt`, `steps`,
  `guidance_scale`, `width`, `height`, `seed` as before, plus optional `image`/`strength`.
- `server/service.py`'s `run_image_job` gained `reference_image_path`/`strength` params; the
  outgoing request to `image-generation/` switched from `json=` to `data=`/`files=` to match.
- `server/app.py`'s `/generate/image` route gained an optional `reference_image` upload (saved to
  the job directory before scheduling the background task, same pattern as `/render/photo`'s mask
  upload) and `strength` form field.
- `server/ui.py`'s Image tab gained a file input + strength number field.
- `python-multipart` added as an explicit dependency of `image-generation/` (needed for FastAPI's
  `Form`/`File` parsing, wasn't needed before when `/generate` only accepted JSON).
- Unit test added: `test_image_job_with_reference_image_sends_multipart_with_strength` -- confirms
  the multipart `data`/`files` payload shape and library provenance, without needing a live GPU.

## A real VRAM investigation, found only by actually running it

First attempt built the img2img pipeline eagerly at startup (`_get_img2img_pipe()` alongside
`_get_pipe()`, reasoning "shares weights, should be free"). That reasoning didn't hold up in
practice: baseline VRAM at rest went from ~7.1GB (text-to-image alone) to ~7.9GB out of 8.19GB
total -- enough to OOM even a *plain text-to-image* request's own activation memory during the
UNet's attention forward pass, before a single img2img request ever happened. `from_pipe()` does
share the big weight tensors, but apparently not for free on a card this tight.

Fixed in three steps, each verified against a real GPU before moving to the next:

1. **Build the img2img pipe lazily** (first actual img2img request, not at startup). Confirmed
   this restored the text-to-image baseline (~7.1GB) and text-to-image worked again. But the
   *first* img2img request itself then failed with a fresh OOM -- this time inside
   `encode_prompt`'s text encoder call, then later inside a VAE-encoder `Conv2d.forward` on a
   retry -- different failure points each time, consistent with "just barely not enough headroom"
   rather than one fixable bug.
2. **`torch.cuda.empty_cache()` after every `/generate` call.** Reduced pressure but didn't fully
   solve it -- a second img2img call, or a plain text-to-image call shortly after an img2img one,
   could still OOM.
3. **`enable_attention_slicing()`** alongside the existing `enable_vae_slicing()` (the UNet
   forward pass was one of the actual OOM sites). This is the combination that got a full clean
   sequence working: text-to-image succeeded, img2img succeeded (real 480x320 output from a
   480x320 reference photo, correctly deriving output size from the input), and a follow-up
   text-to-image call on the same clean-restarted container succeeded too.

**Even with all three mitigations, repeated/rapid calls can still fail.** Stress-testing many
generate calls back-to-back (mixing text-to-image and img2img rapidly) reproduced OOM again,
including one error message with obviously corrupted numbers (`17179869184.00 GiB` -- a sign of
something unstable in PyTorch's own CUDA memory accounting after enough churn in one process, not
a normal "out of memory" report). A clean container restart always recovered.

### Decision: ship as-is, document the limitation (confirmed with the project owner)

Two more aggressive fixes were considered and explicitly not pursued for now:
- Restarting the service after every img2img call (reliable, but a real UX cost -- the ~1-2min
  reload on the next request).
- `enable_sequential_cpu_offload()` (the standard low-VRAM diffusers technique -- more likely to
  fully fix the headroom problem, but restructures device placement, meaningfully slows down
  *every* generation including plain text-to-image, and wasn't verified not to break something
  else).

Given the actual usage pattern (a personal tool, not concurrent/high-frequency requests), the
chosen tradeoff is: img2img works and is genuinely useful, but is best-effort under heavy/rapid
use on this specific 8GB card -- an occasional `CUDA out of memory` is a known, accepted
limitation, not a bug to keep chasing. Documented in `image-generation/README.md`.

## A real process mistake, also found only by actually testing the full chain

After getting the isolated `image-generation/` service working, testing through `core`'s own
`/generate/image` route failed with `422 Unprocessable Entity` and an empty job directory (no
reference file ever saved). Root cause: `image-generation/` had been rebuilt and restarted
multiple times during this work, but `core` itself was never rebuilt after editing
`server/app.py`/`server/service.py` -- it was still running the pre-img2img image, whose old
`run_image_job` sent a JSON body against `image-generation/`'s new Form-only `/generate` endpoint,
which FastAPI correctly rejects. Fixed by rebuilding and recreating `core`
(`docker compose build core && docker compose up -d core`). A reminder that this project's
multi-service architecture means *every* touched service needs its own rebuild, not just the one
that was the focus of the change.

## Open issue found along the way, NOT resolved: `/capabilities` reports `image_generation: false`

While debugging the above, found that `core`'s `GET /capabilities` route intermittently (and at
one point, consistently) reported `image_generation: false` even though `image-generation` was
confirmed healthy and reachable in under 100ms via direct `curl`. Investigated extensively without
finding a root cause:

- Calling `service_available(settings.image_generation_service)` directly in a one-off script
  inside the `core` container -- including replicating the exact 4-call sequence
  `/capabilities`'s own route code uses -- reliably returned `True`, every time.
- Calling the actual decorated route function object directly (`from server.app import
  capabilities; await capabilities(settings)`) also reliably returned `True`.
- Only the real HTTP route, served by the actual running `uvicorn` process, got it wrong --
  inconsistently: sometimes `false` after ~6 seconds (suspiciously close to three
  `service_available` calls each hitting their 2.0s timeout), sometimes no response at all across
  8 consecutive attempts, while `GET /health` (no async service calls) responded instantly and
  reliably throughout the same window.
- Ruled out: stale Docker build (confirmed the container's own `app.py` had the current code),
  duplicate module imports (only one `server._external_service` file path resolved), `HTTP_PROXY`/
  `NO_PROXY` env vars (none set in either context), Docker embedded-DNS staleness from
  `image-generation` being recreated multiple times (reproduced even after a full `docker compose
  down` + fresh `up` of both services together), and general host/Docker resource exhaustion
  (`docker ps` and `GET /health` both responded in well under 100ms during the same window this
  was failing).

This bug predates this session's img2img changes -- `/capabilities`, `service_available`, and the
`capabilities()` route were not touched by any of this work. It matters practically because
`/capabilities` gating is what makes the web UI's Image tab appear at all, so even though the
underlying `POST /generate/image` chain works when reachable, the UI's own visibility signal for
it may currently be unreliable. Explicitly not chased further this session, per the project
owner's own call -- flagged here for a future session with a clearer head, rather than guessing
further right now.

## Verdict

- [x] img2img built and verified working end to end against a real GPU (RTX 4060): a real
  480x320 reference photo produced a real 480x320 output conditioned on it, through the service
  standalone and through `core`'s full `/generate/image` chain.
- [x] Three real VRAM bugs found and mitigated (eager-load OOM, first-img2img-call OOM, repeated-
  call instability) -- accepted as best-effort under heavy use per the project owner's explicit
  decision, not left silently broken.
- [x] Found and fixed a real process mistake (forgot to rebuild `core` after editing its source).
- [ ] Open, unresolved: `core`'s `/capabilities` unreliably reports `image_generation: false` for
  a demonstrably healthy service, root cause not found despite substantial investigation. Left as
  a flagged, documented open issue rather than guessed at further.
