"""Generates a synthetic test photo (a window against a wall) for trying the
`from-photo` effects without needing your own image."""
import cv2
import numpy as np

W, H = 480, 320


def main(output_path="examples/test_photo.jpg"):
    img = np.full((H, W, 3), (60, 45, 35), dtype=np.uint8)
    cv2.rectangle(img, (0, 220), (W, H), (40, 60, 30), -1)  # ground
    cv2.rectangle(img, (100, 50), (380, 220), (90, 110, 140), -1)  # window/sky patch
    cv2.imwrite(str(output_path), img)
    print(f"Wrote {output_path}")
    return output_path


if __name__ == "__main__":
    main()
