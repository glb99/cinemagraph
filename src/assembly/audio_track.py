"""Concatenates music tracks into one track (crossfading between songs,
fading the whole piece in/out at its edges), and layers sound effects
continuously under/over a finished audio track.
"""
from . import ffmpeg_runner


def build_music_track(
    track_paths: list[str], output_path: str, *,
    crossfade_duration: float = 2.0, edge_fade_duration: float = 2.0, gap_duration: float = 0.0,
    run_ffmpeg=ffmpeg_runner.run_ffmpeg, probe_duration=ffmpeg_runner.probe_duration,
) -> None:
    """Concatenates `track_paths` in order. Two mutually exclusive join
    styles between consecutive songs -- `gap_duration > 0` wins if both are
    set, since a silent pause and an overlapping blend are opposite
    concepts, not a spectrum:

    - Default (`gap_duration=0`): crossfade `crossfade_duration` seconds via
      ffmpeg's `acrossfade` filter (chained for 3+ tracks -- unlike `xfade`,
      `acrossfade` doesn't need an explicit offset, it aligns to the tail of
      the running merged stream automatically).
    - `gap_duration > 0`: insert that many seconds of real silence between
      each pair of songs instead -- built via ffmpeg's `concat` filter with
      a synthetic `anullsrc` silent segment spliced between each track
      (`concat` alone only does hard joins with no gap of its own; the
      silence has to be a real input). `layer_sound_effects` (below) mixes
      any sound effects in as a *separate* step over the finished track, so
      effects keep playing continuously straight through this gap -- the
      gap only ever affects the music.

    Either way, the fully-joined track then fades in from silence over
    `edge_fade_duration` seconds at the very start and fades out to silence
    over the same duration at the very end (`afade`) -- so the first song
    doesn't start abruptly at full volume and the last doesn't cut off
    abruptly either. `edge_fade_duration=0` disables the edge fades (same
    "0 to disable" convention `cinemagraph`'s own `--grain` option already
    uses).

    A single track still gets edge fades even with no join needed.
    """
    if not track_paths:
        raise ValueError("build_music_track needs at least one track.")

    stages = []
    if gap_duration > 0 and len(track_paths) > 1:
        inputs, concat_labels = [], []
        input_index = 0
        for i, path in enumerate(track_paths):
            inputs += ["-i", path]
            concat_labels.append(f"{input_index}:a")
            input_index += 1
            if i < len(track_paths) - 1:
                inputs += [
                    "-f", "lavfi", "-t", str(gap_duration),
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                ]
                concat_labels.append(f"{input_index}:a")
                input_index += 1
        concat_refs = "".join(f"[{label}]" for label in concat_labels)
        stages.append(f"{concat_refs}concat=n={len(concat_labels)}:v=0:a=1[joined]")
        prev_label = "joined"
    else:
        inputs = []
        for path in track_paths:
            inputs += ["-i", path]
        if len(track_paths) == 1:
            prev_label = "0:a"
        else:
            prev_label = "0:a"
            for i in range(1, len(track_paths)):
                out_label = f"a{i}"
                stages.append(f"[{prev_label}][{i}:a]acrossfade=d={crossfade_duration}[{out_label}]")
                prev_label = out_label

    if edge_fade_duration > 0:
        total_duration = sum(probe_duration(p) for p in track_paths)
        if gap_duration > 0 and len(track_paths) > 1:
            total_duration += gap_duration * (len(track_paths) - 1)
        else:
            total_duration -= crossfade_duration * (len(track_paths) - 1)
        fade_out_start = max(total_duration - edge_fade_duration, 0.0)
        stages.append(
            f"[{prev_label}]afade=t=in:st=0:d={edge_fade_duration},"
            f"afade=t=out:st={fade_out_start}:d={edge_fade_duration}[out]"
        )
        final_label = "out"
    else:
        final_label = prev_label

    if stages:
        run_ffmpeg([
            "-y", *inputs,
            "-filter_complex", ";".join(stages),
            "-map", f"[{final_label}]",
            output_path,
        ])
    else:
        # One track, no edge fade -- nothing to filter, just copy through.
        run_ffmpeg(["-y", "-i", track_paths[0], "-c", "copy", output_path])


def layer_sound_effects(
    base_audio_path: str, effect_paths: list[str], output_path: str, *,
    run_ffmpeg=ffmpeg_runner.run_ffmpeg, probe_duration=ffmpeg_runner.probe_duration,
) -> None:
    """Mixes `effect_paths` continuously under/over `base_audio_path` via
    ffmpeg's `amix` filter -- each effect loops (`-stream_loop -1`) and is
    capped to the base track's own duration if it's shorter, so a short
    ambience clip still covers the whole piece. Uses `amix`'s default
    normalization (each input scaled to avoid clipping) rather than a
    hand-picked volume balance between music and effects -- not otherwise
    specified, revisit if the default mix reads as too quiet/loud in
    practice.

    No effects given: the base track is copied through untouched.
    """
    if not effect_paths:
        run_ffmpeg(["-y", "-i", base_audio_path, "-c", "copy", output_path])
        return

    base_duration = probe_duration(base_audio_path)

    inputs = ["-i", base_audio_path]
    for effect_path in effect_paths:
        inputs += ["-stream_loop", "-1", "-t", str(base_duration), "-i", effect_path]

    run_ffmpeg([
        "-y", *inputs,
        "-filter_complex", f"amix=inputs={len(effect_paths) + 1}:duration=first:dropout_transition=0[a]",
        "-map", "[a]",
        output_path,
    ])
