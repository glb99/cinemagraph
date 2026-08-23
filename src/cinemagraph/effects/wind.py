"""Turbulent gusting: several octaves of sway-like displacement at increasing
spatial detail and temporal rate, summed together -- unlike `sway`'s single
clean wave, this reads as gusting rather than a uniform tide, since the
octaves drift in and out of phase with each other.
"""

import cv2
import numpy as np

from ._util import cycles_for, rng
from .base import Effect, EffectContext, EffectFamily, register

DEFAULTS = {"amplitude": 8.0, "gustiness": 1.0}


def _precompute(ctx: EffectContext) -> dict:
    amplitude = ctx.kwargs.get("amplitude", DEFAULTS["amplitude"])
    gustiness = ctx.kwargs.get("gustiness", DEFAULTS["gustiness"])

    yy, xx = np.meshgrid(np.arange(ctx.h), np.arange(ctx.w), indexing="ij")
    r = rng(0)
    octaves = 3
    spatial_freqs = np.array([1.0, 2.3, 4.7])[:octaves]
    weights = np.array([1.0, 0.5, 0.25])[:octaves]
    base_hz = np.array([0.2, 0.35, 0.6])[:octaves] * max(gustiness, 0.1)
    cycles = np.array([cycles_for(hz, ctx.duration, ctx.speed) for hz in base_hz])
    phases = r.uniform(0, 2 * np.pi, octaves)
    return {
        "yy": yy,
        "xx": xx,
        "mask": ctx.mask,
        "amplitude": amplitude,
        "h": ctx.h,
        "spatial_freqs": spatial_freqs,
        "weights": weights,
        "cycles": cycles,
        "phases": phases,
    }


def _apply(base, pc, t):
    disp = np.zeros(pc["yy"].shape, dtype=np.float32)
    for freq, wgt, cyc, ph in zip(
        pc["spatial_freqs"], pc["weights"], pc["cycles"], pc["phases"], strict=True
    ):
        disp += wgt * np.sin(
            2 * np.pi * cyc * t + pc["yy"] * freq / pc["h"] * 2 * np.pi + ph
        )
    disp = pc["amplitude"] * disp / pc["weights"].sum() * pc["mask"]
    map_x = (pc["xx"] + disp).astype(np.float32)
    map_y = pc["yy"].astype(np.float32)
    return cv2.remap(
        base,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )


WIND = register(
    Effect(
        name="wind",
        family=EffectFamily.TONE,
        precompute=_precompute,
        apply=_apply,
        defaults=DEFAULTS,
        allowed_kwargs=frozenset({"amplitude", "gustiness"}),
    )
)
