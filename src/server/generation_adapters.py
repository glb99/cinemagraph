"""Concrete adapters implementing generation_ports.py's ports.

SDXLAdapter wraps the same call_optional_service call run_image_job used to
build inline before this refactor -- behavior unchanged, just extracted
behind the ImageGenerator port. GeminiAdapter is the second adapter §3.6
predicted, confirming the port actually earns its keep: it's a hosted API
(no health-check-able satellite container, no `strength` knob) rather than a
self-hosted GPU service, and run_image_job needed zero changes to gain it --
see docs/DESIGN.md sec 3.6.

ACEStepAdapter/StableAudioAdapter are the same extraction applied to music
and sound-effect generation once image generation had proven the pattern
twice over -- each wraps exactly what run_music_job/run_sound_effect_job
used to build inline, no behavior change to the wire contract.

Lyria3Adapter (added 2026-08-01) is MusicGenerator's own GeminiAdapter
equivalent: a second, hosted-API backend registered alongside ACEStepAdapter,
confirmed against a real key (see docs/experiments/2026-08-01-lyria3-adapter.md)
after Lyria RealTime was investigated and rejected the day before for being a
live-streaming-only API with no natural clip boundary
(docs/experiments/2026-07-31-lyria-music-rejected.md) -- Lyria 3 is a genuine
one-shot `prompt in, file out` API, unlike that one. StableAudioAdapter still
has only one registered backend (no second sound-effect API has been found
yet).
"""
import asyncio
import json

from ._external_service import call_optional_service
from .config import Settings

MUSIC_POLL_INTERVAL_SECONDS = 2.0
# 800s total. Not an arbitrary round number -- ACE-Step's own server logs its
# LM "thinking" step's own budget at startup ("Setting constrained decoding
# max_duration to Ns based on GPU config (tier: ...)"); on an 8GB "tier3" GPU
# that's 480s for the LM step alone, confirmed via a real request that got
# wrongly marked errored by this client at the old 300s (150 x 2s) budget
# while the server was still actively computing (worker process's own CPU
# time was sampled twice, 5s apart, and was still climbing -- not hung).
# 800s leaves real margin above that 480s floor for the diffusion+VAE-decode
# steps that run after thinking finishes (a real successful request took
# ~77s past that point). If a lower/higher-tier GPU's own max_duration ever
# needs to be read here instead of hardcoded, that's a genuine future
# improvement, not done now -- see docs/experiments/2026-08-01-acestep-poll-timeout.md.
MUSIC_POLL_MAX_ATTEMPTS = 400


class SDXLAdapter:
    """Talks to image-generation/ (local Stable Diffusion XL)."""

    def __init__(self, settings: Settings, call_service=call_optional_service):
        self._settings = settings
        self._call_service = call_service

    async def generate(
        self,
        prompt: str,
        *,
        reference_image_bytes: bytes | None = None,
        reference_image_filename: str = "reference.png",
        strength: float = 0.6,
    ) -> bytes:
        """`reference_image_bytes`, when given, switches image-generation/
        into img2img mode (StableDiffusionXLImg2ImgPipeline.from_pipe) --
        `strength` (0=stay close to the reference, 1=ignore it) only matters
        in that mode. Always sent as multipart/form-data, matching the
        service's own contract (a plain JSON body can't carry a file upload).
        """
        data = {"prompt": prompt}
        files = None
        if reference_image_bytes is not None:
            data["strength"] = strength
            files = {"image": (reference_image_filename, reference_image_bytes)}

        resp = await self._call_service(
            self._settings.image_generation_service, "POST", "/generate",
            data=data, files=files, timeout=120.0,
        )
        return resp.content


class GeminiAdapter:
    """Talks to Google's Gemini API directly via generation/ -- a hosted API
    needing a key, not a self-hosted container needing a GPU (§3.6's other
    predicted adapter shape). `generation` is imported inside `generate()`,
    not at module level, so importing this module (for SDXLAdapter) never
    requires google-genai to be installed -- only deployments that actually
    set GEMINI_API_KEY (and thus get this adapter registered, see app.py)
    need the `generation` extra synced.

    `strength` is accepted in **kwargs for interface parity with
    SDXLAdapter but silently ignored -- Gemini's own image API has no
    equivalent denoising-strength control (confirmed against the installed
    package, see generation/__init__.py's docstring).
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    async def generate(
        self,
        prompt: str,
        *,
        reference_image_bytes: bytes | None = None,
        reference_image_filename: str = "reference.png",
        strength: float = 0.6,
    ) -> bytes:
        from generation import generate_image

        return await generate_image(
            self._settings.gemini_api_key,
            prompt,
            reference_image_bytes=reference_image_bytes,
            reference_image_filename=reference_image_filename,
        )


class ACEStepAdapter:
    """Drives an ACE-Step API server's own job-queue API to completion:
    create a task, poll until it reports success/failure, download the
    resulting audio. ACE-Step's response envelope wraps everything in
    {"data": ..., "code": ...}; see docs/DESIGN.md / the acestep-docs
    skill's API.md for the full contract.

    ACE-Step's /release_task has no separate "instrumental" field (that only
    exists on its higher-level OpenRouter-compatible wrapper, a different API
    surface than the one this client talks to) -- its own server derives
    instrumental mode purely from the *lyrics string itself*
    (`acestep/api/server_utils.py`'s `is_instrumental`: true iff the
    stripped/lowercased lyrics equal "[inst]" or "[instrumental]"). An empty
    lyrics field does NOT trigger it -- ACE-Step will still attempt vocals.
    `instrumental=True` overrides whatever lyrics text was provided with
    that exact marker, mirroring what ACE-Step's own Gradio UI's
    "Instrumental" checkbox does -- entirely this adapter's own concern, not
    the caller's: a hypothetical second music backend might support
    `instrumental` natively, with no marker hack needed. run_music_job's own
    provenance records the *lyrics actually submitted* by the caller, not
    this internal substitution, for exactly that reason.
    """

    def __init__(self, settings: Settings, call_service=call_optional_service):
        self._settings = settings
        self._call_service = call_service

    async def generate(
        self, prompt: str, *, lyrics: str, duration: float, thinking: bool, instrumental: bool = False
    ) -> bytes:
        service = self._settings.music_service
        effective_lyrics = "[Instrumental]" if instrumental else lyrics

        create_resp = await self._call_service(
            service, "POST", "/release_task",
            json={
                "prompt": prompt, "lyrics": effective_lyrics,
                "audio_duration": duration, "thinking": thinking,
                # ACE-Step's own default is 2 (confirmed in its API.md) -- it
                # generates that many candidate variations per task, but this
                # adapter only ever keeps result[0] (below), so the second
                # candidate was pure wasted GPU time: roughly double the
                # "thinking"/decode and diffusion work for nothing, which was
                # also what pushed real requests close to the poll-timeout
                # ceiling (see docs/experiments/2026-08-01-acestep-poll-timeout.md).
                "batch_size": 1,
            },
        )
        task_id = create_resp.json()["data"]["task_id"]

        result = None
        for _ in range(MUSIC_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(MUSIC_POLL_INTERVAL_SECONDS)
            # timeout=90 (call_optional_service's default is 30): found via a
            # real failure -- on a cold/just-restarted ACE-Step instance, its
            # own model-loading work blocks a single /query_result call
            # synchronously for the full duration of loading + first
            # generation, not just returning a quick "still running" status
            # the way it does once warm. 30s wasn't enough margin for that;
            # steady-state polling (the overwhelmingly common case) is
            # unaffected since those calls return almost immediately either way.
            query_resp = await self._call_service(
                service, "POST", "/query_result",
                json={"task_id_list": [task_id]}, timeout=90.0,
            )
            entry = query_resp.json()["data"][0]
            if entry["status"] == 1:
                result = json.loads(entry["result"])[0]
                break
            if entry["status"] == 2:
                raise RuntimeError("ACE-Step reported generation failure.")
        if result is None:
            raise RuntimeError(f"ACE-Step generation timed out after {MUSIC_POLL_MAX_ATTEMPTS} polls.")

        audio_resp = await self._call_service(service, "GET", result["file"], timeout=60.0)
        return audio_resp.content


class Lyria3Adapter:
    """Talks to Google's Lyria 3 directly via generation/ -- a hosted API
    needing a key, exactly the same shape GeminiAdapter already established
    for images, not a self-hosted queue like ACEStepAdapter. See
    generation/__init__.py's docstring and
    docs/experiments/2026-08-01-lyria3-adapter.md for the real-API findings
    behind every choice below.

    `thinking` is accepted for interface parity with ACEStepAdapter but
    silently ignored -- Lyria 3 has no chain-of-thought-style knob (confirmed
    against the public docs: its own internal prompt-rewriting step has no
    exposed toggle), the same "accept and ignore" precedent GeminiAdapter set
    for `strength`.

    `instrumental=True` is sent as a plain-language instruction in the
    prompt, not ACEStepAdapter's own `"[Instrumental]"` marker substitution --
    Lyria 3 has no equivalent marker to substitute in the first place (no
    separate lyrics field exists at all), so unlike ACEStepAdapter there is
    nothing to override: whatever `lyrics` was submitted is always sent
    as-is, just with an added instruction alongside it. Because of that,
    `instrumental` is a *request*, not a guaranteed override the way
    ACEStepAdapter's marker is -- flagged, not glossed over, since a real
    call without an explicit instruction was seen to auto-classify as
    instrumental on its own, meaning the reverse (an explicit instruction
    being ignored) hasn't been ruled out by any real test yet either.
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    async def generate(
        self, prompt: str, *, lyrics: str, duration: float, thinking: bool, instrumental: bool = False
    ) -> bytes:
        from generation import generate_music

        return await generate_music(
            self._settings.gemini_api_key,
            prompt,
            lyrics=lyrics,
            duration=duration,
            instrumental=instrumental,
        )


class StableAudioAdapter:
    """Talks to sound-effects/ (local Stable Audio Open). Unlike
    ACEStepAdapter's queue, that service answers in one synchronous call.
    """

    def __init__(self, settings: Settings, call_service=call_optional_service):
        self._settings = settings
        self._call_service = call_service

    async def generate(self, prompt: str, *, duration: float) -> bytes:
        resp = await self._call_service(
            self._settings.sound_effect_service, "POST", "/generate",
            json={"prompt": prompt, "duration": duration}, timeout=300.0,
        )
        return resp.content
