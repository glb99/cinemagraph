"""Lofi-style color grading: warm, desaturated, lifted blacks, light grain."""

import click
import cv2
import numpy as np


def lofi_grade(
    frame: np.ndarray, strength: float = 1.0, grain: float = 0.03
) -> np.ndarray:
    """Apply a warm/faded lofi grade. `strength` scales the whole effect 0..1+."""
    # Annotated because `out` deliberately changes dtype as it goes -- float32
    # for the arithmetic, uint8 for the cv2 colour conversions -- so inferring
    # its type from the first assignment makes every later step an error.
    out: np.ndarray = frame.astype(np.float32)

    # Lift blacks for a faded, matte look.
    lift = 12.0 * strength
    out = out * (1 - lift / 255.0) + lift

    # Warm the image: push reds up, pull blues down slightly.
    out[:, :, 2] += 10.0 * strength  # R channel (BGR order)
    out[:, :, 0] -= 6.0 * strength  # B channel

    out = np.clip(out, 0, 255).astype(np.uint8)

    # Desaturate.
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] *= max(0.0, 1 - 0.18 * strength)
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # Gentle vignette.
    h, w = out.shape[:2]
    y, x = np.ogrid[:h, :w]
    cy, cx = h / 2, w / 2
    dist = np.sqrt(((x - cx) / (w / 2)) ** 2 + ((y - cy) / (h / 2)) ** 2)
    vignette = 1 - np.clip(dist - 0.6, 0, 1) * 0.35 * strength
    out = (out.astype(np.float32) * vignette[..., None]).clip(0, 255).astype(np.uint8)

    if grain > 0:
        noise = np.random.normal(0, 255 * grain, out.shape).astype(np.int16)
        out = np.clip(out.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    return out


def apply_grade_to_frames(
    frames: list[np.ndarray], strength: float = 1.0, grain: float = 0.03
) -> list[np.ndarray]:
    with click.progressbar(frames, label="Grading") as bar:
        return [lofi_grade(f, strength=strength, grain=grain) for f in bar]
