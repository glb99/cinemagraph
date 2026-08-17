"""End-to-end smoke tests: each render_*/save_* function actually produces
valid, non-empty, decodable output on the synthetic fixtures. Not asserting
exact pixel values (grain is unseeded, so runs aren't byte-identical) --
just that the integration between pipeline.py, effects/, mask.py, and
io_utils.py hasn't broken.
"""

import cv2
import pytest

from cinemagraph import io_utils, pipeline


def test_render_video_cinemagraph(test_video):
    frames, fps, soft_mask = pipeline.render_video_cinemagraph(test_video)
    assert len(frames) > 0
    assert fps > 0
    assert soft_mask.shape == frames[0].shape[:2]


def test_render_photo_cinemagraph_combined_effects(test_photo):
    frames, fps = pipeline.render_photo_cinemagraph(
        test_photo,
        effect=["dust", "ripple"],
        duration=1.0,
        fps=10,
    )
    assert len(frames) == 10
    assert fps == 10


def test_render_mask_preview(test_video):
    soft_mask = pipeline.render_mask_preview(test_video)
    assert soft_mask.ndim == 2
    assert 0.0 <= soft_mask.min() and soft_mask.max() <= 1.0


def test_save_cinemagraph_video_writes_readable_file(test_video, tmp_path):
    out_path = tmp_path / "out.mp4"
    pipeline.save_cinemagraph_video(test_video, str(out_path))
    assert out_path.exists()

    frames, fps = io_utils.read_frames(str(out_path))
    assert len(frames) > 0
    assert fps > 0


def test_save_cinemagraph_video_uses_browser_compatible_codec(test_video, tmp_path):
    """Regression test for a real bug: cv2.VideoWriter's H.264 encoding
    depends on an OpenH264 DLL most opencv-python wheels don't ship, so it
    silently fell back to "mp4v" (MPEG-4 Part 2) -- a video cv2 itself can
    read back fine (which is exactly why the round-trip test above never
    caught this), but that browsers categorically cannot decode for <video>
    playback, surfacing as a "0-second" unplayable clip in the web UI despite
    the file being perfectly valid. Checks the actual codec tag instead of
    just "did something readable get written".
    """
    out_path = tmp_path / "out.mp4"
    pipeline.save_cinemagraph_video(test_video, str(out_path))

    cap = cv2.VideoCapture(str(out_path))
    fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
    fourcc = (
        "".join(chr((fourcc_int >> 8 * i) & 0xFF) for i in range(4)).lower().strip()
    )
    cap.release()

    # OpenCV reports the tag differently depending on backend/version --
    # "avc1" is the container-level tag ffprobe reports, "h264"/"x264" show
    # up too depending on how it's read back. All three mean H.264; "mp4v"
    # (MPEG-4 Part 2) is the actual regression this test guards against.
    assert fourcc in {"avc1", "h264", "x264"}, (
        f"expected H.264, got {fourcc!r} -- browsers can't play this"
    )


def test_write_video_handles_odd_dimensions(tmp_path):
    """Regression test for a real bug: libx264+yuv420p requires even width and
    height (chroma subsampling halves each dimension). The fix for the mp4v
    bug above passed macro_block_size=1 to preserve exact frame dimensions,
    but that also disables the padding that would otherwise round an odd
    dimension up to even -- so an odd-height frame (e.g. a 1920x1027 photo,
    hit for real via the web UI) made ffmpeg fail outright ("height not
    divisible by 2") instead of producing a broken-pipe write error.
    macro_block_size=2 is the minimum that satisfies libx264 while still only
    padding by at most 1px, instead of the default 16.
    """
    import numpy as np

    frames = [np.zeros((1027, 1920, 3), dtype=np.uint8) for _ in range(3)]
    out_path = tmp_path / "out.mp4"
    io_utils.write_video(frames, str(out_path), fps=10)

    cap = cv2.VideoCapture(str(out_path))
    assert cap.isOpened()
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) > 0
    cap.release()


def test_save_cinemagraph_from_photo_writes_readable_file(test_photo, tmp_path):
    out_path = tmp_path / "out.mp4"
    pipeline.save_cinemagraph_from_photo(
        test_photo, str(out_path), effect="smoke", duration=1.0, fps=10
    )
    assert out_path.exists()

    frames, fps = io_utils.read_frames(str(out_path))
    assert len(frames) == 10
    assert fps == 10


def test_save_mask_preview_writes_valid_png(test_video, tmp_path):
    out_path = tmp_path / "mask.png"
    pipeline.save_mask_preview(test_video, str(out_path))
    assert out_path.exists()

    img = cv2.imread(str(out_path), cv2.IMREAD_GRAYSCALE)
    assert img is not None


def test_loop_duration_and_gif_together_raises(test_photo, tmp_path):
    with pytest.raises(ValueError):
        pipeline.save_cinemagraph_from_photo(
            test_photo,
            str(tmp_path / "out.mp4"),
            effect="smoke",
            duration=1.0,
            fps=10,
            also_gif=True,
            loop_duration=60.0,
        )
