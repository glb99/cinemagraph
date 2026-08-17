"""Candle/fire brightness flicker: sum of sine harmonics modulating brightness."""

import numpy as np

from .. import mask as mask_mod
from ._util import cycles_for
from .base import Effect, EffectContext, EffectFamily, register

DEFAULTS = {"strength": 0.25}


def _precompute(ctx: EffectContext) -> dict:
    strength = ctx.kwargs.get("strength", DEFAULTS["strength"])
    mask3 = mask_mod.to_3ch(ctx.mask)
    base_hz = (
        0.75,
        1.75,
        2.75,
    )  # tuned at 4s (fundamental ~0.75Hz); scaled proportionally
    harmonics = [cycles_for(hz, ctx.duration, ctx.speed) for hz in base_hz]
    return {"mask3": mask3, "harmonics": harmonics, "strength": strength}


def _apply(base, pc, t):
    wave = sum(np.sin(2 * np.pi * k * t + k) for k in pc["harmonics"]) / len(
        pc["harmonics"]
    )
    brightness = 1.0 + pc["strength"] * wave
    return base * (1 - pc["mask3"]) + np.clip(base * brightness, 0, 255) * pc["mask3"]


FLICKER = register(
    Effect(
        name="flicker",
        family=EffectFamily.TONE,
        precompute=_precompute,
        apply=_apply,
        defaults=DEFAULTS,
        allowed_kwargs=frozenset({"strength"}),
    )
)
