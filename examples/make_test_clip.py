"""Generates a synthetic test clip: static background + one moving blob (fake steam)."""
import cv2
import numpy as np

W, H, FPS, N = 320, 240, 30, 60


def main():
    writer = cv2.VideoWriter("examples/test_input.mp4", cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    bg = np.full((H, W, 3), (60, 45, 35), dtype=np.uint8)  # warm dark background
    cv2.rectangle(bg, (40, 150), (280, 220), (90, 70, 50), -1)  # a "table"
    cv2.circle(bg, (160, 130), 30, (30, 30, 200), -1)  # a static "mug"

    for i in range(N):
        frame = bg.copy()
        t = i / N * 2 * np.pi
        for j in range(3):
            offset = j * 8
            y = int(100 - 40 * ((i + offset * 3) % N) / N)
            x = int(160 + 10 * np.sin(t * 2 + j))
            radius = int(6 + 4 * np.sin(t + j))
            cv2.circle(frame, (x, y), max(radius, 1), (200, 200, 200), -1)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        writer.write(frame)
    writer.release()
    print("Wrote examples/test_input.mp4")


if __name__ == "__main__":
    main()
