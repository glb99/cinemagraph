"""Golden-frame regression check.

Not a pytest test on purpose -- this isn't a correctness contract (that's
what tests/ is for), it's a fast, offline way to catch "the refactor
silently changed the rendered pixels" that unit tests wouldn't notice
(e.g. tests/test_pipeline_smoke.py deliberately only checks that output is
*valid*, not that it matches previous output). See docs/DESIGN.md sec 6.

Renders a few representative cases through the real pipeline with grain=0
(the only source of run-to-run randomness in cinemagraph.grade -- every
other step, including particle motion via its seeded RNG, is already
deterministic) and diffs specific frames against blessed PNGs.

Usage:
    uv run python scripts/golden_check.py            # check against golden/
    uv run python scripts/golden_check.py --bless     # (re)generate golden/
"""
import argparse
import importlib.util
import tempfile
from pathlib import Path

import cv2
import numpy as np

from cinemagraph import pipeline

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = ROOT / "tests" / "golden"
TOLERANCE = 1.0  # mean abs pixel diff allowed (0..255 scale) -- covers float/codec rounding


def _load_example_module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cases(tmp_dir: Path):
    """(case_name, frames) pairs -- each frames list is what golden-checks that case."""
    make_test_clip = _load_example_module("make_test_clip")
    make_test_photo = _load_example_module("make_test_photo")

    video_path = make_test_clip.main(tmp_dir / "input.mp4")
    photo_path = make_test_photo.main(tmp_dir / "photo.jpg")

    video_frames, _, _ = pipeline.render_video_cinemagraph(str(video_path), grain=0.0)
    photo_frames, _ = pipeline.render_photo_cinemagraph(
        str(photo_path), effect=["dust", "ripple"], duration=1.0, fps=10, grain=0.0,
    )

    return {
        "video_first": video_frames[0],
        "video_mid": video_frames[len(video_frames) // 2],
        "photo_first": photo_frames[0],
        "photo_mid": photo_frames[len(photo_frames) // 2],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bless", action="store_true", help="(re)generate golden frames instead of checking")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        cases = _cases(Path(tmp))

    if args.bless:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        for name, frame in cases.items():
            cv2.imwrite(str(GOLDEN_DIR / f"{name}.png"), frame)
        print(f"Blessed {len(cases)} golden frames into {GOLDEN_DIR}")
        return 0

    if not GOLDEN_DIR.exists():
        print(f"No golden frames at {GOLDEN_DIR} yet -- run with --bless first.")
        return 1

    failures = []
    for name, frame in cases.items():
        golden_path = GOLDEN_DIR / f"{name}.png"
        if not golden_path.exists():
            failures.append(f"{name}: no golden file at {golden_path} (run --bless)")
            continue
        golden = cv2.imread(str(golden_path))
        if golden.shape != frame.shape:
            failures.append(f"{name}: shape mismatch {frame.shape} vs golden {golden.shape}")
            continue
        diff = np.mean(np.abs(frame.astype(np.int16) - golden.astype(np.int16)))
        status = "OK" if diff <= TOLERANCE else "MISMATCH"
        print(f"{name}: mean diff {diff:.3f} ({status})")
        if diff > TOLERANCE:
            failures.append(f"{name}: mean diff {diff:.3f} > tolerance {TOLERANCE}")

    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"\nAll {len(cases)} golden frames match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
