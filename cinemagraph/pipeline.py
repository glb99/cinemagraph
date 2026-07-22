"""End-to-end cinemagraph pipeline: still + motion mask + loop + grade."""
from pathlib import Path

import click
import cv2
import numpy as np

from . import grade as grade_mod
from . import io_utils
from . import loop as loop_mod
from . import mask as mask_mod
from . import photo_effects


def make_cinemagraph(
    input_path: str,
    output_path: str,
    mask_path: str | None = None,
    still_frame_index: int = 0,
    blend_frames: int = 10,
    auto_trim_loop: bool = True,
    mask_threshold: int = 25,
    feather: int = 21,
    apply_grade: bool = True,
    grade_strength: float = 1.0,
    grain: float = 0.03,
    also_gif: bool = False,
    mask_preview_path: str | None = None,
    loop_duration: float | None = None,
) -> None:
    if loop_duration and also_gif:
        raise ValueError("--gif isn't supported together with --loop-duration (the GIF would be enormous); drop one of them.")

    frames, fps = io_utils.read_frames(input_path)
    if still_frame_index >= len(frames):
        raise ValueError(f"still_frame_index {still_frame_index} out of range (clip has {len(frames)} frames)")

    if auto_trim_loop:
        cut = loop_mod.find_best_loop_point(frames)
        frames = frames[:cut]

    still = frames[still_frame_index].copy()
    h, w = still.shape[:2]

    if mask_path:
        soft_mask = mask_mod.load_mask(mask_path, (h, w), feather=feather)
    else:
        soft_mask = mask_mod.auto_motion_mask(frames, threshold=mask_threshold, feather=feather)

    if mask_preview_path:
        mask_mod.save_mask_preview(soft_mask, mask_preview_path)

    mask_3ch = np.stack([soft_mask] * 3, axis=-1)

    composited = []
    with click.progressbar(frames, label="Compositing") as bar:
        for frame in bar:
            blended = (still.astype(np.float32) * (1 - mask_3ch) + frame.astype(np.float32) * mask_3ch)
            composited.append(blended.astype(np.uint8))

    looped = loop_mod.crossfade_loop(composited, blend_frames=blend_frames)

    if apply_grade:
        looped = grade_mod.apply_grade_to_frames(looped, strength=grade_strength, grain=grain)

    io_utils.write_video(looped, output_path, fps=fps, loop_duration=loop_duration)

    if also_gif:
        gif_path = str(Path(output_path).with_suffix(".gif"))
        io_utils.write_gif(looped, gif_path, fps=fps)


def make_cinemagraph_from_photo(
    photo_path: str,
    output_path: str,
    effect: str | list[str],
    mask_path: str | None = None,
    duration: float = 4.0,
    fps: int = 30,
    speed: float = 1.0,
    feather: int = 21,
    apply_grade: bool = True,
    grade_strength: float = 1.0,
    grain: float = 0.03,
    also_gif: bool = False,
    effect_kwargs: dict | None = None,
    loop_duration: float | None = None,
) -> None:
    """`effect` may be a single effect name, or a list to combine -- at most
    one particle effect (rain/snow/dust) plus any number of tone effects
    (ripple/sway/flicker/smoke). `effect_kwargs` is keyed by effect name for
    per-effect overrides, e.g. {"dust": {"count": 150}}."""
    if loop_duration and also_gif:
        raise ValueError("--gif isn't supported together with --loop-duration (the GIF would be enormous); drop one of them.")

    image = cv2.imread(str(photo_path))
    if image is None:
        raise FileNotFoundError(f"Could not read photo: {photo_path}")

    h, w = image.shape[:2]
    soft_mask = mask_mod.load_mask(mask_path, (h, w), feather=feather) if mask_path else None

    n_frames = max(int(round(duration * fps)), 2)
    frames = photo_effects.animate_photo(
        image, effect=effect, mask=soft_mask, n_frames=n_frames, duration=duration,
        speed=speed, effect_kwargs=effect_kwargs,
    )

    if apply_grade:
        frames = grade_mod.apply_grade_to_frames(frames, strength=grade_strength, grain=grain)

    io_utils.write_video(frames, output_path, fps=fps, loop_duration=loop_duration)

    if also_gif:
        gif_path = str(Path(output_path).with_suffix(".gif"))
        io_utils.write_gif(frames, gif_path, fps=fps)
