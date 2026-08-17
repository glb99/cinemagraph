"""Water-like sine-wave pixel displacement."""

import cv2
import numpy as np

from ._util import cycles_for
from .base import Effect, EffectContext, EffectFamily, register

DEFAULTS = {"amplitude": 4.0, "wavelength": 40.0}


def _precompute(ctx: EffectContext) -> dict:
    amplitude = ctx.kwargs.get("amplitude", DEFAULTS["amplitude"])
    wavelength = ctx.kwargs.get("wavelength", DEFAULTS["wavelength"])
    cycles = cycles_for(
        speed_hz=0.5, duration=ctx.duration, speed=ctx.speed
    )  # ~2 cycles at 4s default
    yy, xx = np.meshgrid(np.arange(ctx.h), np.arange(ctx.w), indexing="ij")
    return {
        "cycles": cycles,
        "yy": yy,
        "xx": xx,
        "mask": ctx.mask,
        "amplitude": amplitude,
        "wavelength": wavelength,
    }


def _apply(base, pc, t):
    phase = 2 * np.pi * pc["cycles"] * t
    disp = (
        pc["amplitude"]
        * np.sin(2 * np.pi * pc["yy"] / pc["wavelength"] + phase)
        * pc["mask"]
    )
    map_x = (pc["xx"] + disp).astype(np.float32)
    map_y = pc["yy"].astype(np.float32)
    return cv2.remap(
        base,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )


RIPPLE = register(
    Effect(
        name="ripple",
        family=EffectFamily.TONE,
        precompute=_precompute,
        apply=_apply,
        defaults=DEFAULTS,
        allowed_kwargs=frozenset({"amplitude", "wavelength"}),
    )
)
