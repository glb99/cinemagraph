"""Concatenates music tracks into one track (crossfading between songs,
fading the whole piece in/out at its edges), and layers sound effects
continuously under/over a finished audio track.
"""
from . import ffmpeg_runner

# silenceremove's threshold is a *linear* amplitude (0..1 for normalized
# float samples), not decibels, despite looking like it should accept a
# "-30dB"-style string (confirmed against the real installed ffmpeg build --
# passing a dB string doesn't error, it just silently produces empty/wrong
# output). 0.02 (~-34dB) reliably catches a genuine faded-to-near-silence
# tail without also catching a real quiet passage in the middle of a song
# (real generated music was found to vary naturally between roughly 0.03
# and 0.25 linear RMS throughout a track, well above this threshold).
_SILENCE_THRESHOLD = 0.02
_SILENCE_MIN_DURATION = 0.1


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
      ffmpeg's `acrossfade` filter with an equal-power curve (`curve1=
      curve2=qsin`, rather than the default linear `tri` -- avoids a slight
      extra loudness dip through the transition on top of the one below;
      chained for 3+ tracks -- unlike `xfade`, `acrossfade` doesn't need an
      explicit offset, it aligns to the tail of the running merged stream
      automatically). Each interior join's own edges are trimmed of
      near-silence first (`silenceremove`) -- real generated music commonly
      fades to near-total silence at its own tail, so crossfading two such
      edges together blends two silences instead of two songs, an audible
      dip found by decoding real output to raw PCM, not by ear.
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
            # Trim each INTERIOR join's own near-silent edges before
            # crossfading -- found via real generated music, not assumed:
            # AI-generated songs commonly fade to near-total silence at
            # their own tail (confirmed by decoding real ACE-Step output to
            # raw PCM and measuring RMS directly -- the last ~1s measured
            # ~40 out of a ~1000-8000 range everywhere else), so a
            # crossfade window landing on two already-faded edges blends
            # two near-silences instead of two songs, producing an audible
            # volume dip -- not a bug in acrossfade itself (verified
            # separately against constant-amplitude synthetic tones, which
            # cross-fade cleanly). Only interior edges are trimmed: the
            # very first track's own leading edge and the very last
            # track's own trailing edge are left alone, since those are
            # edge_fade_duration's job below, not a crossfade join.
            track_labels = []
            for i, path in enumerate(track_paths):
                filters = []
                if i > 0:
                    filters.append(
                        f"silenceremove=start_periods=1:start_threshold={_SILENCE_THRESHOLD}:"
                        f"start_silence={_SILENCE_MIN_DURATION}:detection=rms"
                    )
                if i < len(track_paths) - 1:
                    filters.append(
                        f"silenceremove=stop_periods=-1:stop_threshold={_SILENCE_THRESHOLD}:"
                        f"stop_silence={_SILENCE_MIN_DURATION}:detection=rms"
                    )
                if filters:
                    label = f"trimmed{i}"
                    stages.append(f"[{i}:a]{','.join(filters)}[{label}]")
                    track_labels.append(label)
                else:
                    track_labels.append(f"{i}:a")

            prev_label = track_labels[0]
            for i in range(1, len(track_paths)):
                out_label = f"a{i}"
                stages.append(
                    f"[{prev_label}][{track_labels[i]}]"
                    f"acrossfade=d={crossfade_duration}:curve1=qsin:curve2=qsin[{out_label}]"
                )
                prev_label = out_label

    if edge_fade_duration > 0:
        # Approximate: durations are probed from the original files, before
        # any interior-edge silence trimming above -- a real trim shortens
        # the actual output slightly more than this estimate accounts for.
        # Close enough for "fade out near the end" (trims are on the order
        # of ~1s against tracks tens of seconds long); not worth a second
        # probe pass on the trimmed intermediate audio for that precision.
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
