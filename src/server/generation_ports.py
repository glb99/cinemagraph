"""Ports (interfaces) that generation-backend adapters implement.

See docs/DESIGN.md sec 3.6: server/ defines the contract here,
generation_adapters.py implements it -- Dependency Inversion applied to
generation backends, the same "genuine peers behind one shared call shape"
idea effects/base.py's registry (sec 3.3) already applies to motion effects.
"""
from typing import Protocol


class ImageGenerator(Protocol):
    async def generate(self, prompt: str, **kwargs) -> bytes: ...
