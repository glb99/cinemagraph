"""Mask generation: figure out which pixels should stay animated."""
import click
import cv2
import numpy as np


def auto_motion_mask(
    frames: list[np.ndarray],
    threshold: int = 25,
    dilate_iterations: int = 3,
    feather: int = 21,
) -> np.ndarray:
    """Build a soft mask (float32, 0..1, HxW) from the areas of a clip that move.

    Works by accumulating absolute frame-to-frame differences, thresholding
    the result, cleaning it up with morphology, then feathering the edges so
    the composite doesn't have a hard cutout look.
    """
    gray_frames = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]

    accum = np.zeros_like(gray_frames[0], dtype=np.float32)
    pairs = list(zip(gray_frames, gray_frames[1:]))
    with click.progressbar(pairs, label="Detecting motion") as bar:
        for a, b in bar:
            diff = cv2.absdiff(a, b).astype(np.float32)
            accum += diff

    accum /= max(len(gray_frames) - 1, 1)
    accum = cv2.normalize(accum, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    _, binary = cv2.threshold(accum, threshold, 255, cv2.THRESH_BINARY)

    kernel = np.ones((5, 5), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)  # drop small noise specks
    binary = cv2.dilate(binary, kernel, iterations=dilate_iterations)

    # Keep only the largest connected motion region (avoids stray noise blobs
    # scattered around the frame turning into "hard edge" artifacts).
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if num_labels > 1:
        largest = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
        binary = np.where(labels == largest, 255, 0).astype(np.uint8)

    feather = feather if feather % 2 == 1 else feather + 1  # kernel size must be odd
    soft = cv2.GaussianBlur(binary, (feather, feather), 0)
    return soft.astype(np.float32) / 255.0


def load_mask(mask_path: str, shape: tuple[int, int], feather: int = 21) -> np.ndarray:
    """Load a hand-painted mask (white = moves, black = frozen) and feather it."""
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(f"Could not read mask: {mask_path}")
    h, w = shape
    mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
    feather = feather if feather % 2 == 1 else feather + 1
    mask = cv2.GaussianBlur(mask, (feather, feather), 0)
    return mask.astype(np.float32) / 255.0


def save_mask_preview(mask: np.ndarray, out_path: str) -> None:
    cv2.imwrite(str(out_path), (mask * 255).astype(np.uint8))
