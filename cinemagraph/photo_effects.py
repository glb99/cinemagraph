"""Turn a single still photo into an animated frame sequence using procedural
(non-AI) motion effects: rain, snow, dust motes, water ripple, flicker, smoke,
and sway. Each effect is driven by periodic functions of t in [0, 1), so the
resulting sequence loops perfectly by construction -- no crossfading needed.

Motion rate is defined per-second (via `speed`) rather than per-loop, and
`duration` is a real parameter each effect receives -- so changing the clip's
length doesn't change how fast the motion appears to move; it only changes
how many times the loop plays out per minute of playback.
"""
import click
import cv2
import numpy as np

EFFECTS = ("rain", "snow", "dust", "ripple", "flicker", "smoke", "sway")

# Default per-effect speeds were tuned by eye against a 4s clip. Effects that
# use a whole-cycle-count internally derive that count from
# `speed_hz * duration` so the same `speed` value looks the same regardless
# of `duration`.
REFERENCE_DURATION = 4.0


def _full_mask(h, w):
    return np.ones((h, w), dtype=np.float32)


def _rng(seed):
    return np.random.default_rng(seed)


def _particles(image, mask, n_frames, duration, count, min_speed, max_speed, size_range,
               direction=(0, 1), jitter=2.0, color=(255, 255, 255), seed=0, speed=1.0):
    """Shared engine for dust/rain/snow: particles drifting in a fixed direction,
    looping seamlessly because each particle's position is a function of
    t = frame_index / n_frames wrapped into [0, 1).

    `min_speed`/`max_speed` are tuned assuming a REFERENCE_DURATION-second
    clip; total travel is scaled by duration/REFERENCE_DURATION so a particle's
    apparent pixels-per-second rate stays constant as `duration` changes.
    """
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

    frames = []
    with click.progressbar(range(n_frames), label="Generating frames") as bar:
        for i in bar:
            t = i / n_frames
            layer = np.zeros((h, w, 3), dtype=np.float32)
            travel = base_speed * t * max(h, w) * duration_scale
            px = (x0 + dx * travel + jitter * np.sin(2 * np.pi * t + phase)) % w
            py = (y0 + dy * travel + jitter * np.cos(2 * np.pi * t + phase)) % h
            for x, y, s in zip(px, py, sizes):
                cv2.circle(layer, (int(x), int(y)), max(int(s), 1), color, -1)
            layer = cv2.GaussianBlur(layer, (0, 0), sigmaX=1.2)
            frames.append(layer)
    return frames


def rain(image, mask=None, n_frames=60, duration=REFERENCE_DURATION, count=90, seed=0, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    layers = _particles(image, mask, n_frames, duration, count, min_speed=1.4, max_speed=2.2,
                         size_range=(1, 1), direction=(0.15, 1), jitter=0.5,
                         color=(200, 210, 220), seed=seed, speed=speed)
    out = []
    for base_layer in layers:
        streaked = cv2.GaussianBlur(base_layer, (1, 9), 0)
        out.append(streaked)
    return _composite_additive(image, out, mask, opacity=0.55)


def snow(image, mask=None, n_frames=90, duration=REFERENCE_DURATION, count=70, seed=0, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    layers = _particles(image, mask, n_frames, duration, count, min_speed=0.15, max_speed=0.4,
                         size_range=(1.5, 3.5), direction=(0.2, 1), jitter=6.0,
                         color=(255, 255, 255), seed=seed, speed=speed)
    return _composite_additive(image, layers, mask, opacity=0.85)


def dust(image, mask=None, n_frames=90, duration=REFERENCE_DURATION, count=45, seed=0, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    layers = _particles(image, mask, n_frames, duration, count, min_speed=0.05, max_speed=0.15,
                         size_range=(1, 2.5), direction=(0.1, -1), jitter=4.0,
                         color=(210, 220, 235), seed=seed, speed=speed)
    return _composite_additive(image, layers, mask, opacity=0.4)


def _composite_additive(image, layers, mask, opacity=0.5):
    mask3 = np.stack([mask] * 3, axis=-1)
    base = image.astype(np.float32)
    out = []
    for layer in layers:
        blended = base + layer * opacity * mask3
        out.append(np.clip(blended, 0, 255).astype(np.uint8))
    return out


def _cycles_for(speed_hz, duration, speed):
    """Whole cycle count so the loop closes perfectly, chosen to match
    `speed_hz` cycles/second scaled by the user's `speed` multiplier."""
    return max(1, round(speed_hz * speed * duration))


def ripple(image, mask=None, n_frames=60, duration=REFERENCE_DURATION,
           amplitude=4.0, wavelength=40.0, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    cycles = _cycles_for(speed_hz=0.5, duration=duration, speed=speed)  # ~2 cycles at 4s default
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    frames = []
    with click.progressbar(range(n_frames), label="Generating frames") as bar:
        for i in bar:
            t = i / n_frames
            phase = 2 * np.pi * cycles * t
            disp = amplitude * np.sin(2 * np.pi * yy / wavelength + phase) * mask
            map_x = (xx + disp).astype(np.float32)
            map_y = yy.astype(np.float32)
            warped = cv2.remap(image, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT)
            frames.append(warped)
    return frames


def sway(image, mask=None, n_frames=60, duration=REFERENCE_DURATION,
         amplitude=6.0, freq=1.0, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    cycles = _cycles_for(speed_hz=0.25, duration=duration, speed=speed)  # ~1 cycle at 4s default
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    frames = []
    with click.progressbar(range(n_frames), label="Generating frames") as bar:
        for i in bar:
            t = i / n_frames
            phase = 2 * np.pi * cycles * t
            disp = amplitude * np.sin(phase + yy * freq / h * 2 * np.pi) * mask
            map_x = (xx + disp).astype(np.float32)
            map_y = yy.astype(np.float32)
            warped = cv2.remap(image, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT)
            frames.append(warped)
    return frames


def flicker(image, mask=None, n_frames=60, duration=REFERENCE_DURATION,
            strength=0.25, speed=1.0):
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
    mask3 = np.stack([mask] * 3, axis=-1)
    base = image.astype(np.float32)
    # Base harmonics tuned at 4s (fundamental ~0.75Hz); scale each proportionally.
    base_hz = (0.75, 1.75, 2.75)
    harmonics = [_cycles_for(hz, duration, speed) for hz in base_hz]
    frames = []
    with click.progressbar(range(n_frames), label="Generating frames") as bar:
        for i in bar:
            t = i / n_frames
            wave = sum(np.sin(2 * np.pi * k * t + k) for k in harmonics) / len(harmonics)
            brightness = 1.0 + strength * wave
            lit = base * (1 - mask3) + np.clip(base * brightness, 0, 255) * mask3
            frames.append(np.clip(lit, 0, 255).astype(np.uint8))
    return frames


def smoke(image, mask=None, n_frames=90, duration=REFERENCE_DURATION,
          opacity=0.35, seed=0, speed=1.0):
    """Drifting cloud/mist layer built from a few sine fields.

    Sine fields are smooth and periodic everywhere by construction -- unlike
    scrolling a noise texture with np.roll, there's no seam to hide, because
    each layer's time-phase advances by a whole number of cycles over the
    clip, so frame 0 and the wrap-around point match exactly.
    """
    h, w = image.shape[:2]
    mask = mask if mask is not None else _full_mask(h, w)
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
    base = image.astype(np.float32)
    frames = []
    with click.progressbar(range(n_frames), label="Generating frames") as bar:
        for i in bar:
            t = i / n_frames
            field = np.zeros((h, w), dtype=np.float32)
            for fx, fy, ph, cyc, wgt in zip(freqs_x, freqs_y, phases, cycles, weights):
                field += wgt * np.sin(fx * xx + fy * yy + ph + 2 * np.pi * cyc * t)
            field = field / n_layers  # roughly in [-1, 1]
            smoke_layer = np.stack([field] * 3, axis=-1) * 128.0
            blended = base + smoke_layer * opacity * mask3
            frames.append(np.clip(blended, 0, 255).astype(np.uint8))
    return frames


DISPATCH = {
    "rain": rain,
    "snow": snow,
    "dust": dust,
    "ripple": ripple,
    "sway": sway,
    "flicker": flicker,
    "smoke": smoke,
}


def animate_photo(image: np.ndarray, effect: str, mask: np.ndarray | None = None,
                   n_frames: int = 90, duration: float = REFERENCE_DURATION,
                   **kwargs) -> list[np.ndarray]:
    if effect not in DISPATCH:
        raise ValueError(f"Unknown effect '{effect}'. Choose from: {', '.join(EFFECTS)}")
    return DISPATCH[effect](image, mask=mask, n_frames=n_frames, duration=duration, **kwargs)
