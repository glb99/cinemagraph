"""Concrete ImageGenerator adapters (generation_ports.py).

SDXLAdapter wraps the same call_optional_service call run_image_job used to
build inline before this refactor -- behavior unchanged, just extracted
behind the ImageGenerator port. GeminiAdapter is the second adapter §3.6
predicted, confirming the port actually earns its keep: it's a hosted API
(no health-check-able satellite container, no `strength` knob) rather than a
self-hosted GPU service, and run_image_job needed zero changes to gain it --
see docs/DESIGN.md sec 3.6.
"""
from ._external_service import call_optional_service
from .config import Settings


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
