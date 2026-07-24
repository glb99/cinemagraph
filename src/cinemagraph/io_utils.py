"""Video reading/writing helpers."""
from pathlib import Path

import click
import cv2
import numpy as np


def read_frames(video_path: str, max_frames: int | None = None) -> tuple[list[np.ndarray], float]:
    """Read all frames of a video into memory. Returns (frames, fps).

    Keeping everything in memory is fine for the short clips (a few seconds)
    that cinemagraphs are made from; it keeps the rest of the pipeline simple.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
        if max_frames and len(frames) >= max_frames:
            break
    cap.release()

    if not frames:
        raise ValueError(f"No frames read from: {video_path}")
    return frames, fps


def read_image(image_path: str) -> np.ndarray:
    """Read a single image (BGR, uint8) via OpenCV."""
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    return image


def write_video(frames: list[np.ndarray], out_path: str, fps: float, loop_duration: float | None = None) -> None:
    """Write `frames` as a video. If `loop_duration` (seconds) is longer than the
    natural length of `frames`, the loop is repeated to fill it -- frames are
    written directly to the encoder as they're cycled through, so memory use
    stays proportional to the short loop, not the output duration."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    h, w = frames[0].shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v") if out_path.suffix == ".mp4" else cv2.VideoWriter_fourcc(*"VP80")
    writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"Could not open video writer for: {out_path}")

    total_frames = max(int(round(loop_duration * fps)), len(frames)) if loop_duration else len(frames)
    with click.progressbar(range(total_frames), label="Writing video") as bar:
        for i in bar:
            writer.write(frames[i % len(frames)])
    writer.release()


def write_gif(frames: list[np.ndarray], out_path: str, fps: float) -> None:
    import imageio.v3 as iio

    with click.progressbar(frames, label="Writing GIF") as bar:
        rgb_frames = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in bar]
    duration_ms = 1000.0 / fps
    iio.imwrite(out_path, rgb_frames, duration=duration_ms, loop=0)
