"""Contract tests for mask.py: every mask producer/consumer relies on the
same shape/dtype/range, and 4+ call sites across the codebase broadcast it
to 3 channels via to_3ch() -- these are cheap to check and would otherwise
be the kind of thing that silently breaks in one call site at a time.
"""
import numpy as np

from cinemagraph import mask


def _assert_soft_mask_contract(soft_mask: np.ndarray, h: int, w: int):
    assert soft_mask.dtype == np.float32
    assert soft_mask.shape == (h, w)
    assert soft_mask.min() >= 0.0
    assert soft_mask.max() <= 1.0


def test_auto_motion_mask_contract(test_video):
    from cinemagraph import io_utils

    frames, _ = io_utils.read_frames(test_video)
    h, w = frames[0].shape[:2]
    soft_mask = mask.auto_motion_mask(frames)
    _assert_soft_mask_contract(soft_mask, h, w)
    # The synthetic clip has a moving blob, so *something* should be detected.
    assert soft_mask.max() > 0.1


def test_load_mask_contract(tmp_path):
    import cv2

    mask_path = tmp_path / "mask.png"
    hand_painted = np.zeros((50, 80), dtype=np.uint8)
    hand_painted[10:30, 20:60] = 255
    cv2.imwrite(str(mask_path), hand_painted)

    soft_mask = mask.load_mask(str(mask_path), shape=(100, 160))
    _assert_soft_mask_contract(soft_mask, 100, 160)
    # White region should end up bright after resize+feather; corners should stay dark.
    assert soft_mask[50, 80] > 0.5
    assert soft_mask[0, 0] < 0.1


def test_to_3ch_broadcasts_correctly():
    soft_mask = np.array([[0.0, 1.0], [0.5, 0.25]], dtype=np.float32)
    out = mask.to_3ch(soft_mask)
    assert out.shape == (2, 2, 3)
    for c in range(3):
        assert np.array_equal(out[:, :, c], soft_mask)
