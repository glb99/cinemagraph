"""Concrete ImageGenerator adapters (generation_ports.py).

SDXLAdapter wraps the same call_optional_service call run_image_job used to
build inline before this refactor -- behavior unchanged, just extracted
behind the ImageGenerator port so a future second adapter (a hosted API)
can be registered without touching run_image_job again. See
docs/DESIGN.md sec 3.6.
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
