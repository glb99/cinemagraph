"""Concatenates video clips into one track, crossfading between consecutive
clips instead of hard-cutting.
"""
from . import ffmpeg_runner


def build_video_track(
    clip_paths: list[str], output_path: str, *,
    crossfade_duration: float = 1.0,
    run_ffmpeg=ffmpeg_runner.run_ffmpeg, probe_duration=ffmpeg_runner.probe_duration,
) -> None:
    """Concatenates `clip_paths` in order into one video track at
    `output_path`, crossfading `crossfade_duration` seconds at each join via
    ffmpeg's `xfade` filter (chained for 3+ clips -- each stage's `offset`
    is the running merged duration so far, minus the overlap, since `xfade`
    needs an absolute offset into the filter chain, not a relative one).
    Requires each clip's own duration up front (ffmpeg_runner.probe_duration)
    to compute those offsets -- the concrete reason a hand-built filtergraph
    is needed here rather than ffmpeg's simpler `concat` demuxer, which only
    does hard cuts (no crossfade support).

    A single clip is just copied through untouched (no filtering needed).
    """
    if not clip_paths:
        raise ValueError("build_video_track needs at least one clip.")

    if len(clip_paths) == 1:
        run_ffmpeg(["-y", "-i", clip_paths[0], "-c", "copy", output_path])
        return

    durations = [probe_duration(p) for p in clip_paths]

    inputs: list[str] = []
    for path in clip_paths:
        inputs += ["-i", path]

    running_duration = durations[0]
    prev_label = "0:v"
    filter_stages = []
    for i in range(1, len(clip_paths)):
        offset = running_duration - crossfade_duration
        out_label = "v" if i == len(clip_paths) - 1 else f"v{i}"
        filter_stages.append(
            f"[{prev_label}][{i}:v]xfade=transition=fade:duration={crossfade_duration}:offset={offset}[{out_label}]"
        )
        running_duration = running_duration + durations[i] - crossfade_duration
        prev_label = out_label

    run_ffmpeg([
        "-y", *inputs,
        "-filter_complex", ";".join(filter_stages),
        "-map", f"[{prev_label}]",
        output_path,
    ])
