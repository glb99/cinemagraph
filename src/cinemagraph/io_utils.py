"""Video reading/writing helpers."""

from pathlib import Path

import click
import cv2
import numpy as np


def read_frames(
    video_path: str, max_frames: int | None = None
) -> tuple[list[np.ndarray], float]:
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


def write_video(
    frames: list[np.ndarray],
    out_path: str | Path,
    fps: float,
    loop_duration: float | None = None,
) -> None:
    """Write `frames` as a video. If `loop_duration` (seconds) is longer than the
    natural length of `frames`, the loop is repeated to fill it -- frames are
    written directly to the encoder as they're cycled through, so memory use
    stays proportional to the short loop, not the output duration.

    .mp4 output goes through imageio's ffmpeg plugin (imageio-ffmpeg's bundled
    binary, already a project dependency) with libx264/yuv420p, not
    cv2.VideoWriter. cv2.VideoWriter's H.264 encoding depends on an OpenH264
    DLL that most opencv-python wheels don't ship (patent/licensing reasons),
    so it silently falls back to "mp4v" (MPEG-4 Part 2) -- a real, valid,
    playable-in-VLC video that browsers categorically cannot decode for
    <video> playback, surfacing as a "0-second" unplayable clip in the web UI
    despite the file itself being fine. imageio-ffmpeg's bundled binary has
    libx264 built in regardless of the host's OpenCV install, so this doesn't
    depend on what codecs happen to be available on the machine running this.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = (
        max(int(round(loop_duration * fps)), len(frames))
        if loop_duration
        else len(frames)
    )

    if out_path.suffix == ".mp4":
        import imageio.v2 as imageio

        writer = imageio.get_writer(
            str(out_path),
            fps=fps,
            codec="libx264",
            pixelformat="yuv420p",
            # yuv420p halves each dimension via chroma subsampling, so libx264
            # requires both width and height to be even -- macro_block_size=2 is
            # the minimum that satisfies that (rounds up by at most 1px on an
            # odd dimension), vs. the default 16 which would pad much more than
            # needed. macro_block_size=1 (no padding at all) looks tempting for
            # exact dimensions, but it can leave an odd dimension in place and
            # make libx264 fail outright: "height not divisible by 2".
            macro_block_size=2,
        )
        try:
            with click.progressbar(range(total_frames), label="Writing video") as bar:
                for i in bar:
                    writer.append_data(
                        cv2.cvtColor(frames[i % len(frames)], cv2.COLOR_BGR2RGB)
                    )
        finally:
            writer.close()
    else:
        h, w = frames[0].shape[:2]
        # Present at runtime; absent from opencv's bundled stubs.
        fourcc = cv2.VideoWriter_fourcc(*"VP80")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        cv_writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
        if not cv_writer.isOpened():
            raise RuntimeError(f"Could not open video writer for: {out_path}")
        with click.progressbar(range(total_frames), label="Writing video") as bar:
            for i in bar:
                cv_writer.write(frames[i % len(frames)])
        cv_writer.release()


def write_gif(frames: list[np.ndarray], out_path: str, fps: float) -> None:
    import imageio.v3 as iio

    with click.progressbar(frames, label="Writing GIF") as bar:
        rgb_frames = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in bar]
    duration_ms = 1000.0 / fps
    iio.imwrite(out_path, rgb_frames, duration=duration_ms, loop=0)
