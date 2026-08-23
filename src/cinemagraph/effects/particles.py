"""Particle engine (rain / snow / dust): particles drifting in a fixed
direction, wrapping at screen edges. Independent additive overlay, always
composited after every tone effect -- see base.py's module docstring.
"""

import cv2
import numpy as np

from .. import mask as mask_mod
from ._util import cycles_for, rng
from .base import Effect, EffectContext, EffectFamily, register

PARTICLE_DEFAULTS = {
    "rain": {
        "count": 300,
        "min_speed": 1.4,
        "max_speed": 2.2,
        "size_range": (1, 1),
        "direction": (0.15, 1),
        "jitter": 0.5,
        "color": (200, 210, 220),
        "opacity": 0.55,
        "streak_ksize": (1, 9),
    },
    "snow": {
        "count": 70,
        "min_speed": 0.15,
        "max_speed": 0.4,
        "size_range": (1.5, 3.5),
        "direction": (0.2, 1),
        "jitter": 6.0,
        "color": (255, 255, 255),
        "opacity": 0.85,
        "streak_ksize": None,
    },
    "dust": {
        "count": 45,
        "min_speed": 0.05,
        "max_speed": 0.15,
        "size_range": (1, 2.5),
        "direction": (0.1, -1),
        "jitter": 14.0,
        "color": (210, 220, 235),
        "opacity": 0.4,
        "streak_ksize": None,
    },
}


def _particles_precompute_raw(
    image,
    duration,
    count,
    min_speed,
    max_speed,
    size_range,
    direction,
    jitter,
    color,
    seed,
    speed,
):
    """Precompute each particle's trajectory. Per-axis drift is quantized to a
    whole number of screen widths/heights over the loop (`wraps_x`/`wraps_y`)
    -- the same trick `cycles_for` uses elsewhere -- otherwise a particle's
    raw `base_speed * t` drift doesn't return to its start position at t=1,
    and the tiled loop shows a visible jump."""
    from ._util import REFERENCE_DURATION

    h, w = image.shape[:2]
    r = rng(seed)
    x0 = r.uniform(0, w, count)
    y0 = r.uniform(0, h, count)
    base_speed = r.uniform(min_speed, max_speed, count)
    sizes = r.uniform(*size_range, count)  # ty: ignore[no-matching-overload]
    phase = r.uniform(0, 2 * np.pi, count)

    dx, dy = direction
    norm = np.hypot(dx, dy) or 1.0
    dx, dy = dx / norm, dy / norm

    duration_scale = speed * (duration / REFERENCE_DURATION)
    travel = base_speed * max(h, w) * duration_scale
    wraps_x = np.round(dx * travel / w)
    wraps_y = np.round(dy * travel / h)

    # Quantizing to whole screen-widths/heights (above) is only meaningful when
    # the natural travel is comparable to the screen size (rain/snow: several
    # screen-heights per loop). For slow effects like dust, travel is a small
    # fraction of the screen, so it always rounds to zero wraps -- there's no
    # integer number of screen-widths close to a small drift without either
    # erasing it (0) or overshooting by a full screen traversal (1). So the
    # jitter wobble -- already exactly periodic at any amplitude via a whole
    # cycle count, unlike the wrap-quantized drift -- is the real motion
    # source for slow effects; give it a speed/duration-scaled cycle count so
    # `speed` has a visible effect even when wraps_x/wraps_y round to 0.
    jitter_cycles = cycles_for(speed_hz=0.25, duration=duration, speed=speed)

    return {
        "h": h,
        "w": w,
        "x0": x0,
        "y0": y0,
        "wraps_x": wraps_x,
        "wraps_y": wraps_y,
        "sizes": sizes,
        "phase": phase,
        "jitter": jitter,
        "jitter_cycles": jitter_cycles,
        "color": color,
    }


def _particles_layer(pc, t, streak_ksize=None):
    h, w = pc["h"], pc["w"]
    layer = np.zeros((h, w, 3), dtype=np.float32)
    wobble_phase = 2 * np.pi * pc["jitter_cycles"] * t
    px = (
        pc["x0"]
        + pc["wraps_x"] * w * t
        + pc["jitter"] * np.sin(wobble_phase + pc["phase"])
    ) % w
    py = (
        pc["y0"]
        + pc["wraps_y"] * h * t
        + pc["jitter"] * np.cos(wobble_phase + pc["phase"])
    ) % h
    for x, y, s in zip(px, py, pc["sizes"], strict=True):
        cv2.circle(layer, (int(x), int(y)), max(int(s), 1), pc["color"], -1)
    layer = cv2.GaussianBlur(layer, (0, 0), sigmaX=1.2)
    if streak_ksize:
        layer = cv2.GaussianBlur(layer, streak_ksize, 0)
    return layer


def _make_particle_effect(name: str) -> Effect:
    preset_template = PARTICLE_DEFAULTS[name]

    def _precompute(ctx: EffectContext) -> dict:
        preset = dict(preset_template)
        streak_ksize = preset.pop("streak_ksize")
        default_opacity = preset.pop("opacity")
        opacity = ctx.kwargs.get("opacity", default_opacity)
        if ctx.kwargs.get("count") is not None:
            preset["count"] = ctx.kwargs["count"]
        pc = _particles_precompute_raw(
            ctx.image,
            ctx.duration,
            seed=ctx.kwargs.get("seed", 0),
            speed=ctx.speed,
            **preset,
        )
        return {
            "pc": pc,
            "opacity": opacity,
            "streak_ksize": streak_ksize,
            "mask3": mask_mod.to_3ch(ctx.mask),
        }

    def _apply(base, stage, t):
        layer = _particles_layer(stage["pc"], t, streak_ksize=stage["streak_ksize"])
        return base + layer * stage["opacity"] * stage["mask3"]

    return Effect(
        name=name,
        family=EffectFamily.PARTICLE,
        precompute=_precompute,
        apply=_apply,
        defaults={
            k: v for k, v in preset_template.items() if k in ("count", "opacity")
        },
        allowed_kwargs=frozenset({"count", "opacity", "seed"}),
    )


RAIN = register(_make_particle_effect("rain"))
SNOW = register(_make_particle_effect("snow"))
DUST = register(_make_particle_effect("dust"))
