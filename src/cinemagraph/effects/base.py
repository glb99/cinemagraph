"""The plugin interface: one registry replaces the five parallel structures
(EFFECTS, PARTICLE_EFFECTS/TONE_EFFECTS, PARTICLE_PRESETS/CLOUD_PRESETS,
_TONE_BUILDERS, DISPATCH) the old photo_effects.py module carried for the
same 9 effects.

Two families, one interface. `family` is what the combination loop in
animate_photo() switches on to decide ordering -- TONE effects transform a
shared running frame in sequence; PARTICLE effects are independent additive
overlays that always composite after every tone effect, regardless of the
order effects were requested in. That ordering is load-bearing behavior
carried over unchanged from the original implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import numpy as np


class EffectFamily(str, Enum):
    TONE = "tone"
    PARTICLE = "particle"


@dataclass(frozen=True)
class EffectContext:
    """Everything an effect's precompute needs, gathered once per animate_photo() call."""

    image: np.ndarray
    h: int
    w: int
    mask: np.ndarray
    n_frames: int
    duration: float
    speed: float
    kwargs: dict[str, Any]


PrecomputeFn = Callable[[EffectContext], dict]
ApplyFn = Callable[[np.ndarray, dict, float], np.ndarray]


@dataclass(frozen=True)
class Effect:
    name: str
    family: EffectFamily
    precompute: PrecomputeFn
    apply: ApplyFn
    defaults: dict[str, Any] = field(default_factory=dict)
    allowed_kwargs: frozenset[str] = field(default_factory=frozenset)


_REGISTRY: dict[str, Effect] = {}


def register(effect: Effect) -> Effect:
    if effect.name in _REGISTRY:
        raise ValueError(f"Effect '{effect.name}' already registered")
    _REGISTRY[effect.name] = effect
    return effect


def get(name: str) -> Effect:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown effect '{name}'. Choose from: {', '.join(names())}")


def all_effects() -> dict[str, Effect]:
    return dict(_REGISTRY)


def names() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
