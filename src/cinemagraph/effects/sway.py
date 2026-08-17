"""Swaying branches/leaves: horizontal, height-dependent sine displacement."""

import cv2
import numpy as np

from ._util import cycles_for
from .base import Effect, EffectContext, EffectFamily, register

DEFAULTS = {"amplitude": 6.0, "freq": 1.0}


def _precompute(ctx: EffectContext) -> dict:
    amplitude = ctx.kwargs.get("amplitude", DEFAULTS["amplitude"])
    freq = ctx.kwargs.get("freq", DEFAULTS["freq"])
    cycles = cycles_for(
        speed_hz=0.25, duration=ctx.duration, speed=ctx.speed
    )  # ~1 cycle at 4s default
    yy, xx = np.meshgrid(np.arange(ctx.h), np.arange(ctx.w), indexing="ij")
    return {
        "cycles": cycles,
        "yy": yy,
        "xx": xx,
        "mask": ctx.mask,
        "amplitude": amplitude,
        "freq": freq,
        "h": ctx.h,
    }


def _apply(base, pc, t):
    phase = 2 * np.pi * pc["cycles"] * t
    disp = (
        pc["amplitude"]
        * np.sin(phase + pc["yy"] * pc["freq"] / pc["h"] * 2 * np.pi)
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


SWAY = register(
    Effect(
        name="sway",
        family=EffectFamily.TONE,
        precompute=_precompute,
        apply=_apply,
        defaults=DEFAULTS,
        allowed_kwargs=frozenset({"amplitude", "freq"}),
    )
)
