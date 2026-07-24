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
        test_photo, effect=["dust", "ripple"], duration=1.0, fps=10,
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


def test_save_cinemagraph_from_photo_writes_readable_file(test_photo, tmp_path):
    out_path = tmp_path / "out.mp4"
    pipeline.save_cinemagraph_from_photo(test_photo, str(out_path), effect="smoke", duration=1.0, fps=10)
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
            test_photo, str(tmp_path / "out.mp4"), effect="smoke",
            duration=1.0, fps=10, also_gif=True, loop_duration=60.0,
        )
