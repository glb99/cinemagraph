# FFmpeg — license and corresponding source

This project's Docker images redistribute an **unmodified** FFmpeg executable. FFmpeg is separate
software with its own license, and this notice exists to satisfy that license's requirements. It
says nothing about the rest of this project, which is Apache-2.0 (see the repository's `LICENSE`).

## What is bundled

The binary is not installed by this project's `Dockerfile`; it arrives inside the
[`imageio-ffmpeg`](https://github.com/imageio/imageio-ffmpeg) wheel, which ships a prebuilt FFmpeg
per platform. `imageio-ffmpeg` itself is BSD-2-Clause — that covers its Python wrapper, **not** the
executable, which carries FFmpeg's own terms.

| | Version | Build | Source of the build |
|---|---|---|---|
| Linux (what the Docker images ship) | FFmpeg 7.0.2 | `7.0.2-static` | <https://johnvansickle.com/ffmpeg/> |
| Windows (local installs only) | FFmpeg 7.1 | `7.1-essentials_build` | <https://www.gyan.dev/ffmpeg/builds/> |

Bundled by `imageio-ffmpeg` 0.6.0.

Both builds report the same two decisive flags:

```
configuration: --enable-gpl --enable-version3 ...
```

`--enable-gpl` activates FFmpeg's GPL-licensed optional components, and `--enable-version3`
upgrades the terms to version 3. **These builds are therefore licensed under the GNU General
Public License, version 3**, not the LGPL that a default FFmpeg build would carry. The full text
is in [`COPYING.GPLv3`](COPYING.GPLv3) beside this file.

To verify in any built image — note that FFmpeg is not on `PATH`, it lives inside the Python
package:

```bash
docker run --rm <image> python -c \
  "import imageio_ffmpeg,subprocess; subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-version'])"
```

## Corresponding source

The bundled executables are redistributed **exactly as published upstream**. This project applies
no patches to FFmpeg and does not build it.

The corresponding source for each is the matching upstream FFmpeg release:

- **FFmpeg 7.0.2** (the Linux binary in the Docker images):
  <https://ffmpeg.org/releases/ffmpeg-7.0.2.tar.xz>
- **FFmpeg 7.1** (the Windows binary):
  <https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz>

All FFmpeg releases are listed at <https://ffmpeg.org/releases/>, and development history is at
<https://git.ffmpeg.org/ffmpeg.git>. The build configurations used are the ones printed by
`ffmpeg -version`, reproduced above and obtainable from each build's own page linked in the table.

If any of those links stop resolving, open an issue on this repository and the source will be
provided by another means.

## Why this project's own license is unaffected

This project invokes FFmpeg as a **separate process** (`subprocess`, via
`src/assembly/ffmpeg_runner.py` and `imageio`'s writer), never by linking against its libraries.
Running a separate program at arm's length does not make the caller a derivative work, so this
project's code remains Apache-2.0.

What the GPL does govern is **redistribution of the binary itself** — which is exactly what
publishing a Docker image containing it does, and why this notice ships inside the image at
`/app/licenses/ffmpeg/`.

Running the tool locally from a source checkout distributes nothing and carries no obligation
here.
