"""The one place this package touches subprocess -- see the package
docstring for why shelling out to ffmpeg is deliberate here.
"""

import re
import subprocess

import imageio_ffmpeg

_DURATION_RE = re.compile(r"Duration: (\d+):(\d+):(\d+\.\d+)")


def run_ffmpeg(args: list[str]) -> None:
    """Runs imageio_ffmpeg's bundled ffmpeg binary with `args` (everything
    after the exe path itself), raising RuntimeError with ffmpeg's own
    stderr on non-zero exit -- the standard "degrade with a clear message"
    contract every external call in this project already follows
    (_external_service.call_optional_service's HTTPException(503) is the
    same idea one layer up, at the HTTP boundary instead of the process one).
    """
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run([exe, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def probe_duration(path: str) -> float:
    """Returns a media file's duration in seconds (video or audio).

    imageio-ffmpeg bundles ffmpeg only, not ffprobe, so this parses the
    "Duration: HH:MM:SS.ss" line ffmpeg itself prints to stderr when given
    an input with no output (confirmed against the real bundled binary,
    both video and audio, before writing this -- this project's own
    established habit of verifying against the real thing rather than
    assuming a library's documented behavior). That invocation always exits
    non-zero (no output was requested), which is expected here, not a
    failure -- deliberately bypasses run_ffmpeg's raising behavior instead
    of reusing it.
    """
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run([exe, "-i", path], capture_output=True, text=True)
    match = _DURATION_RE.search(result.stderr)
    if not match:
        raise RuntimeError(f"Could not determine duration of {path}: {result.stderr}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
