"""The one truly subtle invariant in this codebase: every effect's motion is
a periodic function of t = frame/n_frames, quantized to a whole cycle count,
so the loop closes perfectly with zero crossfading. This is easy to break
silently when adding a new effect or refactoring an existing one -- these
tests catch that by checking a frame near the end of the loop is close to
the very first frame (frame 0 IS exactly t=0; the true closing point, t=1,
sits just past the last rendered frame -- see the small residual gap this
allows for below).

Also covers basic registry integrity (no duplicate names, valid families)
and that validation.py's per-effect kwarg checks work against the registry.
"""

import numpy as np
import pytest

from cinemagraph import validation
from cinemagraph.effects import EFFECTS, animate_photo
from cinemagraph.effects.base import EffectFamily, all_effects


def _synthetic_image(h=64, w=96):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (60, 80, 100)
    return img


def test_registry_has_no_duplicate_names_and_valid_families():
    effects = all_effects()
    assert len(effects) == len(EFFECTS)
    for name, effect in effects.items():
        assert effect.name == name
        assert effect.family in (EffectFamily.TONE, EffectFamily.PARTICLE)


@pytest.mark.parametrize("effect_name", ["ripple", "dust"])
def test_loop_closes_seamlessly(effect_name):
    image = _synthetic_image()
    n_frames = 48
    frames = animate_photo(
        image, effect_name, n_frames=n_frames, duration=4.0, speed=1.0
    )
    assert len(frames) == n_frames

    first = frames[0].astype(np.int16)
    last = frames[-1].astype(np.int16)
    # Not exactly equal -- the last rendered frame is at t=(n-1)/n, one step
    # short of the true closing point t=1.0 -- but should be close, the same
    # small residual any playback loop has between its last and first frame.
    mean_diff = np.mean(np.abs(first - last))
    assert mean_diff < 15.0, (
        f"{effect_name}: loop doesn't close cleanly (mean diff {mean_diff:.2f})"
    )


def test_combined_tone_and_particle_effects_render():
    image = _synthetic_image()
    frames = animate_photo(image, ["ripple", "dust"], n_frames=12, duration=2.0)
    assert len(frames) == 12
    assert all(f.shape == image.shape for f in frames)


def test_unknown_effect_raises():
    with pytest.raises(ValueError):
        animate_photo(_synthetic_image(), "not_a_real_effect", n_frames=4)


def test_resolve_effect_kwargs_accepts_valid_override():
    resolved = validation.resolve_effect_kwargs(
        ["ripple"], {"ripple": {"amplitude": 5.0}}
    )
    assert resolved == {"ripple": {"amplitude": 5.0}}


def test_resolve_effect_kwargs_rejects_misattributed_override():
    with pytest.raises(ValueError):
        validation.resolve_effect_kwargs(["dust"], {"ripple": {"amplitude": 5.0}})


def test_resolve_effect_kwargs_rejects_unknown_kwarg_for_effect():
    with pytest.raises(ValueError):
        validation.resolve_effect_kwargs(
            ["ripple"], {"ripple": {"not_a_real_param": 1.0}}
        )
