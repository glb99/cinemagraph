"""Smoke / water vapor: a shared layered-sine-field "cloud" engine.

Layered sine fields are smooth and periodic everywhere by construction --
unlike scrolling a noise texture with np.roll, there's no seam to hide,
because each layer's time-phase advances by a whole number of cycles over
the clip, so frame 0 and the wrap-around point match exactly.
"""
import cv2
import numpy as np

from .. import mask as mask_mod
from .base import Effect, EffectContext, EffectFamily, register
from ._util import cycles_for, rng

# Denser, higher-frequency turbulence than the original smoke (4 layers,
# narrower band) for a more organic, less wallpapery drift.
_PRESETS = {
    "smoke": dict(n_layers=6, freq_range=(0.6, 2.6), hz_range=(0.25, 0.55),
                  opacity=0.35, blur_sigma=0.0, rise_hz=0.0),
    # Water vapor/steam: more, lower-frequency layers (broader/softer blobs),
    # blurred further for diffuseness, lower opacity (subtler than smoke),
    # plus a deterministic upward-traveling carrier wave (`rise_hz`) so it
    # visibly rises rather than just breathing in place.
    "vapor": dict(n_layers=6, freq_range=(0.25, 1.0), hz_range=(0.12, 0.3),
                  opacity=0.22, blur_sigma=3.0, rise_hz=0.2),
}


def _precompute_raw(h, w, mask, duration, opacity, seed, speed, n_layers, freq_range, hz_range,
                     blur_sigma=0.0, rise_hz=0.0):
    """`rise_hz` (vapor only) adds one more deterministic layer with a fixed,
    positive vertical spatial frequency and a whole-cycle temporal rate. A
    wave sin(fy*yy + 2*pi*cyc*t) with fy, cyc > 0 has its crests move toward
    smaller yy (i.e. up, since yy grows downward) as t increases -- a rising
    carrier on top of the random layers' organic texture -- while staying
    exactly periodic like every other layer here."""
    r = rng(seed)
    y = np.linspace(0, 2 * np.pi, h)
    x = np.linspace(0, 2 * np.pi, w)
    yy, xx = np.meshgrid(y, x, indexing="ij")

    freqs_x = r.uniform(*freq_range, n_layers)
    freqs_y = r.uniform(*freq_range, n_layers)
    phases = r.uniform(0, 2 * np.pi, n_layers)
    base_hz = r.uniform(*hz_range, n_layers)
    cycles = np.array([cycles_for(hz, duration, speed) for hz in base_hz])
    weights = r.uniform(0.5, 1.0, n_layers)

    rise_cycles = cycles_for(rise_hz, duration, speed) if rise_hz > 0 else 0

    return {
        "yy": yy, "xx": xx, "freqs_x": freqs_x, "freqs_y": freqs_y, "phases": phases,
        "cycles": cycles, "weights": weights, "mask3": mask_mod.to_3ch(mask), "opacity": opacity,
        "n_layers": n_layers, "blur_sigma": blur_sigma, "rise_cycles": rise_cycles,
    }


def _apply(base, pc, t):
    field = np.zeros_like(pc["yy"])
    for fx, fy, ph, cyc, wgt in zip(pc["freqs_x"], pc["freqs_y"], pc["phases"], pc["cycles"], pc["weights"]):
        field += wgt * np.sin(fx * pc["xx"] + fy * pc["yy"] + ph + 2 * np.pi * cyc * t)
    n_terms = pc["n_layers"]
    if pc["rise_cycles"]:
        field += 0.8 * np.sin(1.2 * pc["yy"] + 2 * np.pi * pc["rise_cycles"] * t)
        n_terms += 1
    field = field / n_terms  # roughly in [-1, 1]
    if pc["blur_sigma"] > 0:
        field = cv2.GaussianBlur(field.astype(np.float32), (0, 0), sigmaX=pc["blur_sigma"])
    cloud_layer = np.stack([field] * 3, axis=-1) * 128.0
    return base + cloud_layer * pc["opacity"] * pc["mask3"]


def _make_cloud_effect(name: str) -> Effect:
    preset_template = _PRESETS[name]

    def _precompute(ctx: EffectContext) -> dict:
        preset = dict(preset_template)
        default_opacity = preset.pop("opacity")
        opacity = ctx.kwargs.get("opacity", default_opacity)
        return _precompute_raw(ctx.h, ctx.w, ctx.mask, ctx.duration, opacity,
                                ctx.kwargs.get("seed", 0), ctx.speed, **preset)

    return Effect(
        name=name, family=EffectFamily.TONE,
        precompute=_precompute, apply=_apply,
        defaults={"opacity": preset_template["opacity"]},
        allowed_kwargs=frozenset({"opacity", "seed"}),
    )


SMOKE = register(_make_cloud_effect("smoke"))
VAPOR = register(_make_cloud_effect("vapor"))
