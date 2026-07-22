"""Turn a single still photo into an animated frame sequence using procedural
(non-AI) motion effects: rain, snow, dust motes, water ripple, flicker, smoke,
and sway. Each effect is driven by periodic functions of t in [0, 1), so the
resulting sequence loops perfectly by construction -- no crossfading needed.

Motion rate is defined per-second (via `speed`) rather than per-loop, and
`duration` is a real parameter each effect receives -- so changing the clip's
length doesn't change how fast the motion appears to move; it only changes
how many times the loop plays out per minute of playback.

Effects can be combined: at most one particle effect (rain/snow/dust) plus
any number of tone effects (ripple/sway/flicker/smoke). Each is split into a
`_*_precompute` (static per-loop setup) and `_*_apply` (per-frame transform)
pair so `animate_photo` can chain them -- tone effects run in sequence on a
shared running frame, then the particle overlay (if any) is composited on
top last, since particles sit visually above the scene regardless of what
warps or brightness shifts happened to it.
"""
import click
import cv2
import numpy as np

EFFECTS = ("rain", "snow", "dust", "ripple", "flicker", "smoke", "sway")
PARTICLE_EFFECTS = ("rain", "snow", "dust")
TONE_EFFECTS = ("ripple", "sway", "flicker", "smoke")

# Default per-effect speeds were tuned by eye against a 4s clip. Effects that
# use a whole-cycle-count internally derive that count from
# `speed_hz * duration` so the same `speed` value looks the same regardless
# of `duration`.
REFERENCE_DURATION = 4.0


def _full_mask(h, w):
    return np.ones((h, w), dtype=np.float32)


def _rng(seed):
    return np.random.default_rng(seed)


def _cycles_for(speed_hz, duration, speed):
    """Whole cycle count so the loop closes perfectly, chosen to match
    `speed_hz` cycles/second scaled by the user's `speed` multiplier."""
    return max(1, round(speed_hz * speed * duration))


# ---------------------------------------------------------------------------
# Particle engine (rain / snow / dust): particles drifting in a fixed
# direction, wrapping at screen edges.
# ---------------------------------------------------------------------------

PARTICLE_PRESETS = {
    "rain": dict(count=90, min_speed=1.4, max_speed=2.2, size_range=(1, 1),
                 direction=(0.15, 1), jitter=0.5, color=(200, 210, 220),
                 opacity=0.55, streak_ksize=(1, 9)),
    "snow": dict(count=70, min_speed=0.15, max_speed=0.4, size_range=(1.5, 3.5),
                 direction=(0.2, 1), jitter=6.0, color=(255, 255, 255),
                 opacity=0.85, streak_ksize=None),
    "dust": dict(count=45, min_speed=0.05, max_speed=0.15, size_range=(1, 2.5),
                 direction=(0.1, -1), jitter=14.0, color=(210, 220, 235),
                 opacity=0.4, streak_ksize=None),
}


def _particles_precompute(image, n_frames, duration, count, min_speed, max_speed, size_range,
                           direction, jitter, color, seed, speed):
    """Precompute each particle's trajectory. Per-axis drift is quantized to a
    whole number of screen widths/heights over the loop (`wraps_x`/`wraps_y`)
    -- the same trick `_cycles_for` uses elsewhere -- otherwise a particle's
    raw `base_speed * t` drift doesn't return to its start position at t=1,
    and the tiled loop shows a visible jump."""
    h, w = image.shape[:2]
    rng = _rng(seed)
    x0 = rng.uniform(0, w, count)
    y0 = rng.uniform(0, h, count)
    base_speed = rng.uniform(min_speed, max_speed, count)
    sizes = rng.uniform(*size_range, count)
    phase = rng.uniform(0, 2 * np.pi, count)

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
    # `--speed` has a visible effect even when wraps_x/wraps_y round to 0.
    jitter_cycles = _cycles_for(speed_hz=0.25, duration=duration, speed=speed)

    return {
        "h": h, "w": w, "x0": x0, "y0": y0, "wraps_x": wraps_x, "wraps_y": wraps_y,
        "sizes": sizes, "phase": phase, "jitter": jitter, "jitter_cycles": jitter_cycles, "color": color,
    }


def _particles_layer(pc, t, streak_ksize=None):
    h, w = pc["h"], pc["w"]
    layer = np.zeros((h, w, 3), dtype=np.float32)
    wobble_phase = 2 * np.pi * pc["jitter_cycles"] * t
    px = (pc["x0"] + pc["wraps_x"] * w * t + pc["jitter"] * np.sin(wobble_phase + pc["phase"])) % w
    py = (pc["y0"] + pc["wraps_y"] * h * t + pc["jitter"] * np.cos(wobble_phase + pc["phase"])) % h
    for x, y, s in zip(px, py, pc["sizes"]):
        cv2.circle(layer, (int(x), int(y)), max(int(s), 1), pc["color"], -1)
    layer = cv2.GaussianBlur(layer, (0, 0), sigmaX=1.2)
    if streak_ksize:
        layer = cv2.GaussianBlur(layer, streak_ksize, 0)
    return layer


def _particle_stage_precompute(effect, image, n_frames, duration, mask, speed=1.0, seed=0, count=None, opacity=None):
    preset = dict(PARTICLE_PRESETS[effect])
    streak_ksize = preset.pop("streak_ksize")
    default_opacity = preset.pop("opacity")
    opacity = opacity if opacity is not None else default_opacity
    if count is not None:
        preset["count"] = count
    pc = _particles_precompute(image, n_frames, duration, seed=seed, speed=speed, **preset)
    mask3 = np.stack([mask] * 3, axis=-1)
    return pc, opacity, streak_ksize, mask3


def _particle_stage_apply(base, stage, t):
    pc, opacity, streak_ksize, mask3 = stage
    layer = _particles_layer(pc, t, streak_ksize=streak_ksize)
    return base + layer * opacity * mask3


def rain(image, mask=None, n_frames=60, duration=REFERENCE_DURATION, count=90, seed=0, speed=1.0, opacity=None):
    return animate_photo(image, "rain", mask=mask, n_frames=n_frames, duration=duration,
                          effect_kwargs={"rain": {"count": count, "seed": seed, "opacity": opacity}}, speed=speed)


def snow(image, mask=None, n_frames=90, duration=REFERENCE_DURATION, count=70, seed=0, speed=1.0, opacity=None):
    return animate_photo(image, "snow", mask=mask, n_frames=n_frames, duration=duration,
                          effect_kwargs={"snow": {"count": count, "seed": seed, "opacity": opacity}}, speed=speed)


def dust(image, mask=None, n_frames=90, duration=REFERENCE_DURATION, count=45, seed=0, speed=1.0, opacity=None):
    return animate_photo(image, "dust", mask=mask, n_frames=n_frames, duration=duration,
                          effect_kwargs={"dust": {"count": count, "seed": seed, "opacity": opacity}}, speed=speed)


# ---------------------------------------------------------------------------
# Tone effects (ripple / sway / flicker / smoke): transform the running base
# frame in place rather than adding an independent overlay, so they compose
# by simply running one after another.
# ---------------------------------------------------------------------------

def _ripple_precompute(h, w, mask, duration, amplitude, wavelength, speed):
    cycles = _cycles_for(speed_hz=0.5, duration=duration, speed=speed)  # ~2 cycles at 4s default
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    return {"cycles": cycles, "yy": yy, "xx": xx, "mask": mask, "amplitude": amplitude, "wavelength": wavelength}


def _ripple_apply(base, pc, t):
    phase = 2 * np.pi * pc["cycles"] * t
    disp = pc["amplitude"] * np.sin(2 * np.pi * pc["yy"] / pc["wavelength"] + phase) * pc["mask"]
    map_x = (pc["xx"] + disp).astype(np.float32)
    map_y = pc["yy"].astype(np.float32)
    return cv2.remap(base, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def ripple(image, mask=None, n_frames=60, duration=REFERENCE_DURATION,
           amplitude=4.0, wavelength=40.0, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    return animate_photo(image, "ripple", mask=mask, n_frames=n_frames, duration=duration,
                          effect_kwargs={"ripple": {"amplitude": amplitude, "wavelength": wavelength}}, speed=speed)


def _sway_precompute(h, w, mask, duration, amplitude, freq, speed):
    cycles = _cycles_for(speed_hz=0.25, duration=duration, speed=speed)  # ~1 cycle at 4s default
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    return {"cycles": cycles, "yy": yy, "xx": xx, "mask": mask, "amplitude": amplitude, "freq": freq, "h": h}


def _sway_apply(base, pc, t):
    phase = 2 * np.pi * pc["cycles"] * t
    disp = pc["amplitude"] * np.sin(phase + pc["yy"] * pc["freq"] / pc["h"] * 2 * np.pi) * pc["mask"]
    map_x = (pc["xx"] + disp).astype(np.float32)
    map_y = pc["yy"].astype(np.float32)
    return cv2.remap(base, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def sway(image, mask=None, n_frames=60, duration=REFERENCE_DURATION,
         amplitude=6.0, freq=1.0, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    return animate_photo(image, "sway", mask=mask, n_frames=n_frames, duration=duration,
                          effect_kwargs={"sway": {"amplitude": amplitude, "freq": freq}}, speed=speed)


def _flicker_precompute(mask, duration, strength, speed):
    mask3 = np.stack([mask] * 3, axis=-1)
    base_hz = (0.75, 1.75, 2.75)  # tuned at 4s (fundamental ~0.75Hz); scaled proportionally
    harmonics = [_cycles_for(hz, duration, speed) for hz in base_hz]
    return {"mask3": mask3, "harmonics": harmonics, "strength": strength}


def _flicker_apply(base, pc, t):
    wave = sum(np.sin(2 * np.pi * k * t + k) for k in pc["harmonics"]) / len(pc["harmonics"])
    brightness = 1.0 + pc["strength"] * wave
    return base * (1 - pc["mask3"]) + np.clip(base * brightness, 0, 255) * pc["mask3"]


def flicker(image, mask=None, n_frames=60, duration=REFERENCE_DURATION,
            strength=0.25, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    return animate_photo(image, "flicker", mask=mask, n_frames=n_frames, duration=duration,
                          effect_kwargs={"flicker": {"strength": strength}}, speed=speed)


def _smoke_precompute(h, w, mask, duration, opacity, seed, speed):
    """Sine fields are smooth and periodic everywhere by construction -- unlike
    scrolling a noise texture with np.roll, there's no seam to hide, because
    each layer's time-phase advances by a whole number of cycles over the
    clip, so frame 0 and the wrap-around point match exactly."""
    rng = _rng(seed)
    y = np.linspace(0, 2 * np.pi, h)
    x = np.linspace(0, 2 * np.pi, w)
    yy, xx = np.meshgrid(y, x, indexing="ij")

    n_layers = 4
    freqs_x = rng.uniform(0.6, 2.2, n_layers)
    freqs_y = rng.uniform(0.6, 2.2, n_layers)
    phases = rng.uniform(0, 2 * np.pi, n_layers)
    base_hz = rng.uniform(0.25, 0.5, n_layers)  # tuned at 4s -> 1-2 cycles
    cycles = np.array([_cycles_for(hz, duration, speed) for hz in base_hz])
    weights = rng.uniform(0.5, 1.0, n_layers)

    mask3 = np.stack([mask] * 3, axis=-1)
    return {
        "yy": yy, "xx": xx, "freqs_x": freqs_x, "freqs_y": freqs_y, "phases": phases,
        "cycles": cycles, "weights": weights, "mask3": mask3, "opacity": opacity, "n_layers": n_layers,
    }


def _smoke_apply(base, pc, t):
    field = np.zeros_like(pc["yy"])
    for fx, fy, ph, cyc, wgt in zip(pc["freqs_x"], pc["freqs_y"], pc["phases"], pc["cycles"], pc["weights"]):
        field += wgt * np.sin(fx * pc["xx"] + fy * pc["yy"] + ph + 2 * np.pi * cyc * t)
    field = field / pc["n_layers"]  # roughly in [-1, 1]
    smoke_layer = np.stack([field] * 3, axis=-1) * 128.0
    return base + smoke_layer * pc["opacity"] * pc["mask3"]


def smoke(image, mask=None, n_frames=90, duration=REFERENCE_DURATION,
          opacity=0.35, seed=0, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    return animate_photo(image, "smoke", mask=mask, n_frames=n_frames, duration=duration,
                          effect_kwargs={"smoke": {"opacity": opacity, "seed": seed}}, speed=speed)


_TONE_BUILDERS = {
    "ripple": lambda h, w, mask, duration, speed, kw: (
        _ripple_precompute(h, w, mask, duration, kw.get("amplitude", 4.0), kw.get("wavelength", 40.0), speed),
        _ripple_apply,
    ),
    "sway": lambda h, w, mask, duration, speed, kw: (
        _sway_precompute(h, w, mask, duration, kw.get("amplitude", 6.0), kw.get("freq", 1.0), speed),
        _sway_apply,
    ),
    "flicker": lambda h, w, mask, duration, speed, kw: (
        _flicker_precompute(mask, duration, kw.get("strength", 0.25), speed),
        _flicker_apply,
    ),
    "smoke": lambda h, w, mask, duration, speed, kw: (
        _smoke_precompute(h, w, mask, duration, kw.get("opacity", 0.35), kw.get("seed", 0), speed),
        _smoke_apply,
    ),
}


DISPATCH = {
    "rain": rain,
    "snow": snow,
    "dust": dust,
    "ripple": ripple,
    "sway": sway,
    "flicker": flicker,
    "smoke": smoke,
}


def animate_photo(image: np.ndarray, effect, mask: np.ndarray | None = None,
                   n_frames: int = 90, duration: float = REFERENCE_DURATION,
                   speed: float = 1.0, effect_kwargs: dict | None = None) -> list[np.ndarray]:
    """Render `effect` (a single effect name, or a list to combine) onto `image`.

    At most one particle effect (rain/snow/dust) may be combined with any
    number of tone effects (ripple/sway/flicker/smoke); tone effects are
    applied in the given order to a running base frame, then the particle
    overlay (if any) is composited on top last. `effect_kwargs` is keyed by
    effect name, e.g. {"dust": {"count": 150}, "flicker": {"strength": 0.3}}.
    """
    effects = [effect] if isinstance(effect, str) else list(effect)
    if not effects:
        raise ValueError("At least one effect must be given.")
    for e in effects:
        if e not in EFFECTS:
            raise ValueError(f"Unknown effect '{e}'. Choose from: {', '.join(EFFECTS)}")

    particle_effects = [e for e in effects if e in PARTICLE_EFFECTS]
    tone_effects = [e for e in effects if e in TONE_EFFECTS]
    if len(particle_effects) > 1:
        raise ValueError(f"Only one particle effect (rain/snow/dust) can be combined at a time, got {particle_effects}")

    effect_kwargs = effect_kwargs or {}
    h, w = image.shape[:2]
    mask_arr = mask if mask is not None else _full_mask(h, w)

    tone_stages = [
        _TONE_BUILDERS[e](h, w, mask_arr, duration, speed, effect_kwargs.get(e, {}))
        for e in tone_effects
    ]

    particle_stage = None
    if particle_effects:
        peffect = particle_effects[0]
        kw = effect_kwargs.get(peffect, {})
        particle_stage = _particle_stage_precompute(
            peffect, image, n_frames, duration, mask_arr,
            speed=speed, seed=kw.get("seed", 0), count=kw.get("count"), opacity=kw.get("opacity"),
        )

    frames = []
    label = "Generating frames (" + "+".join(effects) + ")"
    with click.progressbar(range(n_frames), label=label) as bar:
        for i in bar:
            t = i / n_frames
            base = image.astype(np.float32)
            for pc, apply_fn in tone_stages:
                base = apply_fn(base, pc, t)
            if particle_stage:
                base = _particle_stage_apply(base, particle_stage, t)
            frames.append(np.clip(base, 0, 255).astype(np.uint8))
    return frames
