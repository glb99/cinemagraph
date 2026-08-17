"""Turn a single still photo into an animated frame sequence using procedural
(non-AI) motion effects: rain, snow, dust motes, water ripple, flicker,
smoke, water vapor, wind, and sway. Each effect is driven by periodic
functions of t in [0, 1), so the resulting sequence loops perfectly by
construction -- no crossfading needed.

Motion rate is defined per-second (via `speed`) rather than per-loop, and
`duration` is a real parameter each effect receives -- so changing the
clip's length doesn't change how fast the motion appears to move; it only
changes how many times the loop plays out per minute of playback.

Effects can be combined: any mix of particle (rain/snow/dust) and tone
(ripple/sway/flicker/smoke/vapor/wind) effects. Each is registered (see
base.py) as an `Effect(name, family, precompute, apply, defaults,
allowed_kwargs)` so `animate_photo` can chain any number of them: tone
effects run in sequence on a shared running frame, then the particle
overlay (if any) is composited on top last, since particles sit visually
above the scene regardless of what warps or brightness shifts happened to
it.
"""

import click
import numpy as np

from . import base

# Importing these registers each effect as a side effect of module load.
# Aliased so they don't shadow the same-named convenience wrappers defined
# below (rain/snow/dust/ripple/sway/wind/flicker/smoke/vapor).
from . import clouds as _clouds_mod  # noqa: F401
from . import flicker as _flicker_mod  # noqa: F401
from . import particles as _particles_mod  # noqa: F401
from . import ripple as _ripple_mod  # noqa: F401
from . import sway as _sway_mod  # noqa: F401
from . import wind as _wind_mod  # noqa: F401
from .base import Effect, EffectContext, EffectFamily

EFFECTS = base.names()


def animate_photo(
    image: np.ndarray,
    effect,
    mask: np.ndarray | None = None,
    n_frames: int = 90,
    duration: float = 4.0,
    speed: float = 1.0,
    effect_kwargs: dict | None = None,
    unmasked_effects=None,
) -> list[np.ndarray]:
    """Render `effect` (a single effect name, or a list to combine) onto `image`.

    Any number of effects can be combined, in any mix of particle
    (rain/snow/dust) and tone (ripple/sway/flicker/smoke/vapor/wind)
    effects. Tone effects run first, in the given order, transforming a
    shared running frame; particle overlays (if any) always composite last,
    in the given order among themselves -- particles sit visually on top of
    the scene, so running a warp (ripple/sway) after adding them would
    incorrectly smear the particles along with the background.
    `effect_kwargs` is keyed by effect name, e.g. {"dust": {"count": 150},
    "flicker": {"strength": 0.3}}.

    `unmasked_effects` (effect names) opt specific effects out of `mask`
    entirely -- they animate over the whole frame regardless of what `mask`
    says, while every other requested effect still respects it. Lets e.g.
    snow fall over the whole photo while flicker stays confined to a masked
    region, in one call.
    """
    effects = [effect] if isinstance(effect, str) else list(effect)
    if not effects:
        raise ValueError("At least one effect must be given.")

    resolved = [base.get(e) for e in effects]
    tone = [e for e in resolved if e.family is EffectFamily.TONE]
    particle = [e for e in resolved if e.family is EffectFamily.PARTICLE]

    effect_kwargs = effect_kwargs or {}
    unmasked_effects = set(unmasked_effects or ())
    h, w = image.shape[:2]
    mask_arr = mask if mask is not None else np.ones((h, w), dtype=np.float32)
    full_frame = np.ones((h, w), dtype=np.float32)

    def ctx(e: Effect) -> EffectContext:
        return EffectContext(
            image=image,
            h=h,
            w=w,
            mask=full_frame if e.name in unmasked_effects else mask_arr,
            n_frames=n_frames,
            duration=duration,
            speed=speed,
            kwargs=effect_kwargs.get(e.name, {}),
        )

    tone_stages = [(e.apply, e.precompute(ctx(e))) for e in tone]
    particle_stages = [(e.apply, e.precompute(ctx(e))) for e in particle]

    frames = []
    label = "Generating frames (" + "+".join(effects) + ")"
    with click.progressbar(range(n_frames), label=label) as bar:
        for i in bar:
            t = i / n_frames
            b = image.astype(np.float32)
            for apply_fn, pc in tone_stages:
                b = apply_fn(b, pc, t)
            for apply_fn, pc in particle_stages:
                b = apply_fn(b, pc, t)
            frames.append(np.clip(b, 0, 255).astype(np.uint8))
    return frames


def _make_single_effect_wrapper(name: str):
    def wrapper(image, mask=None, n_frames=90, duration=4.0, speed=1.0, **kwargs):
        return animate_photo(
            image,
            name,
            mask=mask,
            n_frames=n_frames,
            duration=duration,
            speed=speed,
            effect_kwargs={name: kwargs},
        )

    wrapper.__name__ = name
    wrapper.__doc__ = (
        f"Render just the '{name}' effect. See animate_photo() for the general case."
    )
    return wrapper


rain = _make_single_effect_wrapper("rain")
snow = _make_single_effect_wrapper("snow")
dust = _make_single_effect_wrapper("dust")
ripple = _make_single_effect_wrapper("ripple")
sway = _make_single_effect_wrapper("sway")
wind = _make_single_effect_wrapper("wind")
flicker = _make_single_effect_wrapper("flicker")
smoke = _make_single_effect_wrapper("smoke")
vapor = _make_single_effect_wrapper("vapor")
