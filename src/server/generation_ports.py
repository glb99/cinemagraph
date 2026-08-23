"""Ports (interfaces) that generation-backend adapters implement.

See docs/DESIGN.md sec 3.6: server/ defines the contract here,
generation_adapters.py implements it -- Dependency Inversion applied to
generation backends, the same "genuine peers behind one shared call shape"
idea effects/base.py's registry (sec 3.3) already applies to motion effects.
"""

from typing import Literal, Protocol


class ImageGenerator(Protocol):
    async def generate(
        self,
        prompt: str,
        *,
        reference_image_bytes: bytes | None = None,
        reference_image_filename: str = "reference.png",
        strength: float = 0.6,
    ) -> bytes:
        """Spelled out rather than `**kwargs`, matching MusicGenerator below.
        A `**kwargs` protocol promises callers may pass anything, which no
        adapter actually honours -- so every adapter failed to satisfy its own
        port, and the registry's type guarantee was decorative. `strength`
        only means anything in img2img mode; an adapter with no equivalent
        (GeminiAdapter) accepts and ignores it, which is the parity this
        signature exists to state."""
        ...


class MusicGenerator(Protocol):
    async def generate(
        self,
        prompt: str,
        *,
        lyrics: str,
        duration: float,
        thinking: bool,
        instrumental: bool = False,
    ) -> bytes: ...

    async def remix(
        self,
        prompt: str,
        *,
        task_type: Literal["text2music", "cover", "repaint"],
        src_audio_bytes: bytes | None = None,
        reference_audio_bytes: bytes | None = None,
        lyrics: str = "",
        duration: float | None = None,
        cover_strength: float = 1.0,
        repainting_start: float = 0.0,
        repainting_end: float = -1.0,
        thinking: bool = False,
    ) -> bytes:
        """Source/reference-audio-conditioned generation: `cover` (restyle an
        existing song), `repaint` (regenerate a time range of one), or plain
        `text2music` with `reference_audio_bytes` set (style transfer,
        independent of task_type). A second method, not new `generate()`
        kwargs -- unlike `thinking`/`strength` (concepts a peer adapter can
        plausibly ignore and still perform the *same* requested task), a
        peer with no source-audio-conditioning capability at all has no
        graceful way to honor this; see ACEStepAdapter/Lyria3Adapter's own
        docstrings.
        """
        ...


class SoundEffectGenerator(Protocol):
    async def generate(self, prompt: str, *, duration: float) -> bytes: ...
