"""Registries of generation-backend adapters -- same shape as effects/base.py's
_REGISTRY (sec 3.3), applied to generation backends instead of motion
effects. See docs/DESIGN.md sec 3.6.

One registry per capability (image/music/sound-effect), not one shared
generic registry -- each capability's registry lives here because server/
owns all three job-routing concerns already, but they stay independent
dicts/functions because §3.3's own registry rule requires genuine peers
(same call shape), and an ImageGenerator/MusicGenerator/SoundEffectGenerator
are not interchangeable with each other, only within their own capability.

Only "sdxl" (image), "acestep" (music), and "stable-audio" (sound effect)
are registered today (app.py, at import time) -- for music/sound-effect,
that's the *only* adapter for now (no second backend exists yet, so nothing
looks these up by name per-request either -- no "model" field on
POST /generate/music or /generate/sound-effect). Image generation went
through this same "mechanism present, unconsumed" phase before GeminiAdapter
existed; see docs/DESIGN.md sec 3.6 for why the registry is still worth
having even with a single adapter (the payoff is at the next real backend,
not the first one).
"""
from .generation_ports import ImageGenerator, MusicGenerator, SoundEffectGenerator

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


_MUSIC_GENERATORS: dict[str, MusicGenerator] = {}


def register_music_generator(name: str, adapter: MusicGenerator) -> None:
    _MUSIC_GENERATORS[name] = adapter


def get_music_generator(name: str) -> MusicGenerator:
    try:
        return _MUSIC_GENERATORS[name]
    except KeyError:
        raise KeyError(
            f"No music generator registered as '{name}'. "
            f"Available: {', '.join(_MUSIC_GENERATORS) or '(none)'}"
        )


def available_music_generators() -> tuple[str, ...]:
    return tuple(_MUSIC_GENERATORS)


_SOUND_EFFECT_GENERATORS: dict[str, SoundEffectGenerator] = {}


def register_sound_effect_generator(name: str, adapter: SoundEffectGenerator) -> None:
    _SOUND_EFFECT_GENERATORS[name] = adapter


def get_sound_effect_generator(name: str) -> SoundEffectGenerator:
    try:
        return _SOUND_EFFECT_GENERATORS[name]
    except KeyError:
        raise KeyError(
            f"No sound effect generator registered as '{name}'. "
            f"Available: {', '.join(_SOUND_EFFECT_GENERATORS) or '(none)'}"
        )


def available_sound_effect_generators() -> tuple[str, ...]:
    return tuple(_SOUND_EFFECT_GENERATORS)
