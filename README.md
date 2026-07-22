# cinemagraph-tool

Generate lofi-style cinemagraphs (frozen background + one looping moving element), entirely from the command line — either from a short video clip, or from a single still photo using built-in procedural motion effects.

## Setup

```bash
pip install -r requirements.txt
```

## From a single photo (no video needed)

Animate a still photo with a procedural effect — motion is generated algorithmically, no source footage required:

```bash
python cli.py from-photo photo.jpg output.mp4 --effect smoke
```

Available effects: `rain`, `snow`, `dust`, `ripple`, `sway`, `flicker`, `smoke`.

By default the effect applies over the whole photo. To restrict it to one area (e.g. only the window, only the candle), supply a mask PNG (white = animated, black = frozen) — paint one by hand in any image editor:

```bash
python cli.py from-photo photo.jpg output.mp4 --effect rain --mask window_mask.png
```

Useful flags: `--duration` (seconds), `--fps`, `--feather` (mask edge softness), `--no-grade`, `--grade-strength`, `--grain`, `--gif`.

These loop perfectly by construction (the motion is a periodic function of time), so there's no crossfading step needed like the video pipeline below.

## From a video clip

Auto-detect the moving region (steam, rain, hair, etc.) and produce a looping, lofi-graded cinemagraph:

```bash
python cli.py make input.mp4 output.mp4
```

Preview the auto-detected mask before committing to a full render (useful for tuning):

```bash
python cli.py mask-preview input.mp4 mask_preview.png
```

Open `mask_preview.png` in an image editor, touch it up if needed (white = keeps moving, black = frozen), then use it directly:

```bash
python cli.py make input.mp4 output.mp4 --mask mask_preview.png
```

Useful flags:

| Flag | What it does |
|---|---|
| `--still-frame N` | which frame becomes the frozen background |
| `--blend-frames N` | how many frames are crossfaded to make the loop seamless |
| `--mask-threshold N` | sensitivity of auto motion detection (lower = more sensitive) |
| `--feather N` | softness of the mask edges (avoids a "cutout" look) |
| `--no-grade` | skip the lofi color grade, keep original colors |
| `--grade-strength X` | intensity of the warm/faded/desaturated grade |
| `--grain X` | film grain amount, 0 to disable |
| `--gif` | also export a `.gif` next to the `.mp4` |
| `--save-mask path.png` | save the mask used, for inspection |

## How the photo effects work

Each effect (`cinemagraph/photo_effects.py`) drives motion with a periodic function of `t = frame / total_frames`, so frame 0 and the "next" frame after the last one line up automatically:

- **rain / snow / dust** — particle systems with different speed, size, direction, and opacity presets.
- **ripple** — sine-wave pixel displacement (`cv2.remap`) for water-like distortion.
- **sway** — the same displacement technique but horizontal and height-dependent, for swaying branches/leaves.
- **flicker** — brightness modulated by a sum of sine harmonics, for candle/fire light.
- **smoke** — a low-res noise texture upscaled for smooth cloud shapes, scrolled with wraparound.

## How the video pipeline works

1. Reads all frames of the input clip.
2. Auto-trims the clip to the point where it loops most naturally (or use the full clip with `--no-auto-trim`).
3. Picks one frame as the frozen background.
4. Builds a soft mask of the moving region — either automatically (frame-differencing + largest connected motion blob + feathering) or from a hand-painted PNG you supply.
5. Composites: frozen background everywhere the mask is black, live video where it's white.
6. Crossfades the last few frames into the first few so the loop point is invisible.
7. Applies a warm, desaturated, lifted-black, grainy "lofi" grade (optional).
8. Exports MP4 (and optionally GIF).

## Tips for good source footage

- Use a tripod — any camera shake breaks the "frozen" illusion.
- Pick one small, naturally cyclical motion: steam, rain on glass, a spinning record, a candle flame, swaying leaves.
- A shot that returns close to its starting position (rotational/oscillating motion) loops far more convincingly than one that doesn't.
- 4-8 seconds of footage is usually enough.

## Testing without your own footage

`examples/make_test_clip.py` generates a synthetic clip (a mug with rising "steam") to try the video pipeline:

```bash
python examples/make_test_clip.py
python cli.py make examples/test_input.mp4 examples/test_output.mp4 --gif
```

`examples/make_test_photo.py` generates a synthetic photo to try the photo effects:

```bash
python examples/make_test_photo.py
python cli.py from-photo examples/test_photo.jpg examples/test_output.mp4 --effect smoke
```
