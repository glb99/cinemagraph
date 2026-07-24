"""Small helpers shared across effect modules."""
import numpy as np

# Default per-effect speeds were tuned by eye against a 4s clip. Effects that
# use a whole-cycle-count internally derive that count from
# `speed_hz * duration` so the same `speed` value looks the same regardless
# of `duration`.
REFERENCE_DURATION = 4.0


def full_mask(h: int, w: int) -> np.ndarray:
    return np.ones((h, w), dtype=np.float32)


def rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def cycles_for(speed_hz: float, duration: float, speed: float) -> int:
    """Whole cycle count so the loop closes perfectly, chosen to match
    `speed_hz` cycles/second scaled by the user's `speed` multiplier."""
    return max(1, round(speed_hz * speed * duration))
