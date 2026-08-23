"""Smoke / water vapor: a shared layered-sine-field "cloud" engine.

Layered sine fields are smooth and periodic everywhere by construction --
unlike scrolling a noise texture with np.roll, there's no seam to hide,
because each layer's time-phase advances by a whole number of cycles over
the clip, so frame 0 and the wrap-around point match exactly.

Also warps the base image via a second, independent low-frequency
displacement field (see `flow_amplitude`/_apply) -- true advection (pixels
actually move), not just the brightness-only modulation `cloud_layer` below
provides on its own. See docs/DESIGN.md sec 5.4: "true advection for
smoke/vapor (translation, not in-place brightness modulation)" was a known
gap -- brightness modulation alone reads as a shimmering overlay with no
real motion; this adds the motion `ripple.py`'s own `cv2.remap` warp already
proved out for water.
"""

import cv2
import numpy as np

from .. import mask as mask_mod
from ._util import cycles_for, rng
from .base import Effect, EffectContext, EffectFamily, register

# Denser, higher-frequency turbulence than the original smoke (4 layers,
# narrower band) for a more organic, less wallpapery drift.
_PRESETS = {
    "smoke": {
        "n_layers": 6,
        "freq_range": (0.6, 2.6),
        "hz_range": (0.25, 0.55),
        "opacity": 0.35,
        "blur_sigma": 0.0,
        "rise_hz": 0.0,
        "flow_amplitude": 4.0,
    },
    # Water vapor/steam: more, lower-frequency layers (broader/softer blobs),
    # blurred further for diffuseness, lower opacity (subtler than smoke),
    # plus a deterministic upward-traveling carrier wave (`rise_hz`) so it
    # visibly rises rather than just breathing in place. Smaller flow
    # amplitude than smoke -- vapor is meant to read as delicate/subtle, not
    # turbulent; the existing rise_hz brightness carrier already covers most
    # of its upward-motion cue, so the warp field only needs to add texture.
    "vapor": {
        "n_layers": 6,
        "freq_range": (0.25, 1.0),
        "hz_range": (0.12, 0.3),
        "opacity": 0.22,
        "blur_sigma": 3.0,
        "rise_hz": 0.2,
        "flow_amplitude": 2.5,
    },
}


def _precompute_raw(
    h,
    w,
    mask,
    duration,
    opacity,
    seed,
    speed,
    n_layers,
    freq_range,
    hz_range,
    blur_sigma=0.0,
    rise_hz=0.0,
    flow_amplitude=0.0,
):
    """`rise_hz` (vapor only) adds one more deterministic layer with a fixed,
    positive vertical spatial frequency and a whole-cycle temporal rate. A
    wave sin(fy*yy + 2*pi*cyc*t) with fy, cyc > 0 has its crests move toward
    smaller yy (i.e. up, since yy grows downward) as t increases -- a rising
    carrier on top of the random layers' organic texture -- while staying
    exactly periodic like every other layer here.

    `flow_amplitude` (see module docstring) drives a second, independent
    low-spatial-frequency displacement field used in _apply to warp the base
    image via cv2.remap -- deliberately a *separate* random draw (own
    frequency/phase/rate) from the brightness field above, so the visible
    drift direction doesn't track the brightness texture 1:1, which would
    read as too uniform/mechanical (real smoke's density and motion aren't
    perfectly correlated either).
    """
    r = rng(seed)
    y = np.linspace(0, 2 * np.pi, h)
    x = np.linspace(0, 2 * np.pi, w)
    yy, xx = np.meshgrid(y, x, indexing="ij")
    py, px = np.meshgrid(
        np.arange(h), np.arange(w), indexing="ij"
    )  # pixel coords for remap

    freqs_x = r.uniform(*freq_range, n_layers)  # ty: ignore[no-matching-overload]
    freqs_y = r.uniform(*freq_range, n_layers)  # ty: ignore[no-matching-overload]
    phases = r.uniform(0, 2 * np.pi, n_layers)
    base_hz = r.uniform(*hz_range, n_layers)  # ty: ignore[no-matching-overload]
    cycles = np.array([cycles_for(hz, duration, speed) for hz in base_hz])
    weights = r.uniform(0.5, 1.0, n_layers)

    rise_cycles = cycles_for(rise_hz, duration, speed) if rise_hz > 0 else 0

    flow_hz_x, flow_hz_y = r.uniform(0.08, 0.2, 2)
    flow_freq_x, flow_freq_y = r.uniform(0.4, 0.9, 2)
    flow_phase_x, flow_phase_y = r.uniform(0, 2 * np.pi, 2)

    return {
        "yy": yy,
        "xx": xx,
        "py": py,
        "px": px,
        "freqs_x": freqs_x,
        "freqs_y": freqs_y,
        "phases": phases,
        "cycles": cycles,
        "weights": weights,
        "mask3": mask_mod.to_3ch(mask),
        "opacity": opacity,
        "n_layers": n_layers,
        "blur_sigma": blur_sigma,
        "rise_cycles": rise_cycles,
        "flow_amplitude": flow_amplitude,
        "flow_cycles_x": cycles_for(flow_hz_x, duration, speed),
        "flow_cycles_y": cycles_for(flow_hz_y, duration, speed),
        "flow_freq_x": flow_freq_x,
        "flow_freq_y": flow_freq_y,
        "flow_phase_x": flow_phase_x,
        "flow_phase_y": flow_phase_y,
    }


def _apply(base, pc, t):
    # Advection first: warp the base image so smoke/vapor visibly drifts and
    # curls, then layer the brightness/density modulation on top of the
    # *warped* result below -- together they read as moving, textured smoke;
    # either alone doesn't (pure warp looks like a rippling mirror, pure
    # brightness modulation looks like a shimmering overlay with no real
    # motion). Same cv2.remap technique ripple.py already uses for water.
    if pc["flow_amplitude"] > 0:
        dx = pc["flow_amplitude"] * np.sin(
            pc["flow_freq_x"] * pc["xx"]
            + pc["flow_phase_x"]
            + 2 * np.pi * pc["flow_cycles_x"] * t
        )
        dy = pc["flow_amplitude"] * np.sin(
            pc["flow_freq_y"] * pc["yy"]
            + pc["flow_phase_y"]
            + 2 * np.pi * pc["flow_cycles_y"] * t
        )
        map_x = (pc["px"] + dx).astype(np.float32)
        map_y = (pc["py"] + dy).astype(np.float32)
        base = cv2.remap(
            base,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )

    field = np.zeros_like(pc["yy"])
    for fx, fy, ph, cyc, wgt in zip(
        pc["freqs_x"],
        pc["freqs_y"],
        pc["phases"],
        pc["cycles"],
        pc["weights"],
        strict=True,
    ):
        field += wgt * np.sin(fx * pc["xx"] + fy * pc["yy"] + ph + 2 * np.pi * cyc * t)
    n_terms = pc["n_layers"]
    if pc["rise_cycles"]:
        field += 0.8 * np.sin(1.2 * pc["yy"] + 2 * np.pi * pc["rise_cycles"] * t)
        n_terms += 1
    field = field / n_terms  # roughly in [-1, 1]
    if pc["blur_sigma"] > 0:
        field = cv2.GaussianBlur(
            field.astype(np.float32), (0, 0), sigmaX=pc["blur_sigma"]
        )
    cloud_layer = np.stack([field] * 3, axis=-1) * 128.0
    return base + cloud_layer * pc["opacity"] * pc["mask3"]


def _make_cloud_effect(name: str) -> Effect:
    preset_template = _PRESETS[name]

    def _precompute(ctx: EffectContext) -> dict:
        preset = dict(preset_template)
        default_opacity = preset.pop("opacity")
        opacity = ctx.kwargs.get("opacity", default_opacity)
        return _precompute_raw(
            ctx.h,
            ctx.w,
            ctx.mask,
            ctx.duration,
            opacity,
            ctx.kwargs.get("seed", 0),
            ctx.speed,
            **preset,
        )

    return Effect(
        name=name,
        family=EffectFamily.TONE,
        precompute=_precompute,
        apply=_apply,
        defaults={"opacity": preset_template["opacity"]},
        allowed_kwargs=frozenset({"opacity", "seed"}),
    )


SMOKE = register(_make_cloud_effect("smoke"))
VAPOR = register(_make_cloud_effect("vapor"))
