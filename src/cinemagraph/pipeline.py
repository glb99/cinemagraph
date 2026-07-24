"""End-to-end cinemagraph pipeline: still + motion mask + loop + grade.

Split into a compute layer (`render_*`, pure-ish: takes paths/params in,
returns frames as data, never touches the output path) and a persist layer
(`save_*`, thin wrappers that call the matching `render_*` then write the
result to disk). This split exists so a future API layer can get frames
back without writing to disk (preview, streaming, custom output handling)
without duplicating the actual rendering logic -- the CLI keeps using the
`save_*` wrappers and its behavior is unchanged.
"""
from pathlib import Path

import click
import numpy as np

from . import effects
from . import grade as grade_mod
from . import io_utils
from . import loop as loop_mod
from . import mask as mask_mod

_LOOP_DURATION_GIF_ERROR = (
    "--gif isn't supported together with --loop-duration (the GIF would be enormous); drop one of them."
)


# ---------------------------------------------------------------------------
# Compute layer: returns frames (and any other data), no disk writes.
# ---------------------------------------------------------------------------

def render_video_cinemagraph(
    input_path: str,
    mask_path: str | None = None,
    still_frame_index: int = 0,
    blend_frames: int = 10,
    auto_trim_loop: bool = True,
    mask_threshold: int = 25,
    feather: int = 21,
    apply_grade: bool = True,
    grade_strength: float = 1.0,
    grain: float = 0.03,
) -> tuple[list[np.ndarray], float, np.ndarray]:
    """Returns (looped_frames, fps, soft_mask_used)."""
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

    mask_3ch = mask_mod.to_3ch(soft_mask)

    composited = []
    with click.progressbar(frames, label="Compositing") as bar:
        for frame in bar:
            blended = (still.astype(np.float32) * (1 - mask_3ch) + frame.astype(np.float32) * mask_3ch)
            composited.append(blended.astype(np.uint8))

    looped = loop_mod.crossfade_loop(composited, blend_frames=blend_frames)

    if apply_grade:
        looped = grade_mod.apply_grade_to_frames(looped, strength=grade_strength, grain=grain)

    return looped, fps, soft_mask


def render_photo_cinemagraph(
    photo_path: str,
    effect: str | list[str],
    mask_path: str | None = None,
    duration: float = 4.0,
    fps: int = 30,
    speed: float = 1.0,
    feather: int = 21,
    apply_grade: bool = True,
    grade_strength: float = 1.0,
    grain: float = 0.03,
    effect_kwargs: dict | None = None,
) -> tuple[list[np.ndarray], float]:
    """`effect` may be a single effect name, or a list to combine -- any mix
    of tone effects (ripple/sway/wind/flicker/smoke/vapor) plus at most one
    particle effect (rain/snow/dust). `effect_kwargs` is keyed by effect
    name for per-effect overrides, e.g. {"dust": {"count": 150}}. Returns
    (frames, fps)."""
    image = io_utils.read_image(photo_path)
    h, w = image.shape[:2]
    soft_mask = mask_mod.load_mask(mask_path, (h, w), feather=feather) if mask_path else None

    n_frames = max(int(round(duration * fps)), 2)
    frames = effects.animate_photo(
        image, effect=effect, mask=soft_mask, n_frames=n_frames, duration=duration,
        speed=speed, effect_kwargs=effect_kwargs,
    )

    if apply_grade:
        frames = grade_mod.apply_grade_to_frames(frames, strength=grade_strength, grain=grain)

    return frames, fps


def render_mask_preview(input_path: str, mask_threshold: int = 25, feather: int = 21) -> np.ndarray:
    """Returns the auto-detected soft mask (float32 HxW, 0..1) for a video clip."""
    frames, _ = io_utils.read_frames(input_path)
    return mask_mod.auto_motion_mask(frames, threshold=mask_threshold, feather=feather)


# ---------------------------------------------------------------------------
# Persist layer: thin wrappers, write to disk, return None.
# ---------------------------------------------------------------------------

def save_cinemagraph_video(
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
        raise ValueError(_LOOP_DURATION_GIF_ERROR)

    frames, fps, soft_mask = render_video_cinemagraph(
        input_path, mask_path=mask_path, still_frame_index=still_frame_index,
        blend_frames=blend_frames, auto_trim_loop=auto_trim_loop, mask_threshold=mask_threshold,
        feather=feather, apply_grade=apply_grade, grade_strength=grade_strength, grain=grain,
    )

    if mask_preview_path:
        mask_mod.save_mask_preview(soft_mask, mask_preview_path)

    io_utils.write_video(frames, output_path, fps=fps, loop_duration=loop_duration)
    if also_gif:
        io_utils.write_gif(frames, str(Path(output_path).with_suffix(".gif")), fps=fps)


def save_cinemagraph_from_photo(
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
    if loop_duration and also_gif:
        raise ValueError(_LOOP_DURATION_GIF_ERROR)

    frames, out_fps = render_photo_cinemagraph(
        photo_path, effect=effect, mask_path=mask_path, duration=duration, fps=fps, speed=speed,
        feather=feather, apply_grade=apply_grade, grade_strength=grade_strength, grain=grain,
        effect_kwargs=effect_kwargs,
    )

    io_utils.write_video(frames, output_path, fps=out_fps, loop_duration=loop_duration)
    if also_gif:
        io_utils.write_gif(frames, str(Path(output_path).with_suffix(".gif")), fps=out_fps)


def save_mask_preview(input_path: str, output_path: str, mask_threshold: int = 25, feather: int = 21) -> None:
    soft_mask = render_mask_preview(input_path, mask_threshold=mask_threshold, feather=feather)
    mask_mod.save_mask_preview(soft_mask, output_path)
