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
used to build inline, no behavior change to the wire contract. Only one
adapter is registered for each (no second music/sound-effect backend exists
yet) -- same "mechanism present, not speculatively populated" stance the
image port started with before GeminiAdapter existed.
"""
import asyncio
import json

from ._external_service import call_optional_service
from .config import Settings

MUSIC_POLL_INTERVAL_SECONDS = 2.0
MUSIC_POLL_MAX_ATTEMPTS = 150  # ~5 minutes total


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
