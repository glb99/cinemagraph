"""Seamless looping of a short frame sequence."""

import cv2
import numpy as np


def crossfade_loop(
    frames: list[np.ndarray], blend_frames: int = 10
) -> list[np.ndarray]:
    """Blend the tail of the clip into the head so the loop point is invisible.

    Replaces the last `blend_frames` frames with a crossfade between the
    original tail and the clip's own start, so frame[-1] flows into frame[0].
    """
    n = len(frames)
    blend_frames = min(blend_frames, n // 2)
    if blend_frames <= 0:
        return frames

    out = [f.copy() for f in frames[: n - blend_frames]]
    for i in range(blend_frames):
        alpha = (i + 1) / (blend_frames + 1)  # 0 -> mostly tail, 1 -> mostly head
        tail_frame = frames[n - blend_frames + i]
        head_frame = frames[i]
        blended = cv2.addWeighted(tail_frame, 1 - alpha, head_frame, alpha, 0)
        out.append(blended)
    return out


def find_best_loop_point(frames: list[np.ndarray], search_last_n: int = 30) -> int:
    """Find the frame index (near the end) most similar to frame 0.

    Useful for trimming a clip to the point where it naturally loops best,
    before crossfading. Returns an index; slice frames[:index] to use it.
    """
    if len(frames) <= search_last_n:
        return len(frames)

    first_gray = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)
    start = len(frames) - search_last_n
    best_idx = len(frames)
    best_score = float("inf")
    for i in range(start, len(frames)):
        gray = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)
        score = np.mean(cv2.absdiff(first_gray, gray))
        if score < best_score:
            best_score = score
            best_idx = i
    return best_idx
