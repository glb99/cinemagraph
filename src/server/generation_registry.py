"""Registry of ImageGenerator adapters -- same shape as effects/base.py's
_REGISTRY (sec 3.3), applied to generation backends instead of motion
effects. See docs/DESIGN.md sec 3.6.

Only "sdxl" is registered today (app.py, at import time) -- the registry
exists so a second adapter (a hosted API) can be added later without
touching run_image_job again, not because a second one exists yet. Nothing
looks this up by name per-request yet either: that's the "model" field on
POST /generate/image, deferred until a second adapter actually justifies it.
"""
from .generation_ports import ImageGenerator

_IMAGE_GENERATORS: dict[str, ImageGenerator] = {}


def register_image_generator(name: str, adapter: ImageGenerator) -> None:
    _IMAGE_GENERATORS[name] = adapter


def get_image_generator(name: str) -> ImageGenerator:
    try:
        return _IMAGE_GENERATORS[name]
    except KeyError:
        raise KeyError(
            f"No image generator registered as '{name}'. "
            f"Available: {', '.join(_IMAGE_GENERATORS) or '(none)'}"
        )


def available_image_generators() -> tuple[str, ...]:
    return tuple(_IMAGE_GENERATORS)
