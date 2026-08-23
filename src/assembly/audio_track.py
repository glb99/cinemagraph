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
    track_paths: list[str],
    output_path: str,
    *,
    crossfade_duration: float = 5.0,
    edge_fade_duration: float = 2.0,
    gap_duration: float = 0.0,
    run_ffmpeg=ffmpeg_runner.run_ffmpeg,
) -> None:
    """Concatenates `track_paths` in order. `crossfade_duration` defaults to
    5.0s, not a shorter value, because of a real, measured tradeoff: even
    after trimming true silence (below), real generated songs still vary
    naturally in loudness near their own edges -- overlapping two such
    non-silent-but-quieter regions still produces a real dip, just a
    shallower one the longer the overlap window is (confirmed by measuring
    RMS through the transition at 2s vs. 6s crossfade duration against the
    same real songs -- the dip's depth relative to the surrounding level
    was roughly 5-10x worse at 2s than at 6s). A short crossfade makes that
    dip a larger fraction of the transition and thus more audible; 5s is a
    practical default, not a full fix -- see `docs/experiments/
    2026-07-31-crossfade-silence-bug.md`'s second follow-up.

    Two mutually exclusive join styles between consecutive songs --
    `gap_duration > 0` wins if both are set, since a silent pause and an
    overlapping blend are opposite concepts, not a spectrum:

    - Default (`gap_duration=0`): crossfade `crossfade_duration` seconds via
      ffmpeg's `acrossfade` filter with an equal-power curve (`curve1=
      curve2=qsin`, rather than the default linear `tri` -- avoids a slight
      extra loudness dip through the transition on top of the one below;
      chained for 3+ tracks -- unlike `xfade`, `acrossfade` doesn't need an
      explicit offset, it aligns to the tail of the running merged stream
      automatically).
    - `gap_duration > 0`: insert that many seconds of real silence between
      each pair of songs instead -- built via ffmpeg's `concat` filter with
      a synthetic `anullsrc` silent segment spliced between each track
      (`concat` alone only does hard joins with no gap of its own; the
      silence has to be a real input). Every gap-adjacent edge also gets its
      own `edge_fade_duration`-length fade into/out of that silence (found
      via real user feedback: without it, each track's trimmed edge butts
      directly against real silence with no transition at all -- an
      audible hard cut, unlike crossfade mode where `acrossfade` itself
      already blends smoothly). `layer_sound_effects` (below) mixes any
      sound effects in as a *separate* step over the finished track, so
      effects keep playing continuously straight through this gap -- the
      gap (and its fades) only ever affect the music.

    Before either join style, **every** track's own leading/trailing
    near-silence is trimmed first (`silenceremove`) -- found via real
    generated music, not assumed: AI-generated songs commonly fade to
    near-total silence at their own edges (confirmed by decoding real
    ACE-Step output to raw PCM and measuring RMS directly -- one real
    track's last ~1s measured ~40 out of a ~1000-8000 range elsewhere;
    another's last ~6s faded out entirely). Left untrimmed, that baked-in
    silence does two different kinds of damage depending on where it sits:
    at an *interior* join it blends two already-near-silent edges instead
    of two songs (an audible dip); at the very first/last track's *outer*
    edge it silently extends however far past where `edge_fade_duration`'s
    own controlled fade actually starts, reading as the music stopping (or
    starting) well before the edge fade you asked for -- not a bug in
    `acrossfade`/`afade` themselves (both verified separately against
    constant-amplitude synthetic tones, which behave exactly as expected).
    Trimming every track's own edges uniformly means `edge_fade_duration`
    always controls the actual edges of the finished piece, never whatever
    leftover silence one specific source recording happens to already have.

    The fully-joined track then fades in from silence over
    `edge_fade_duration` seconds at the very start and fades out to silence
    over the same duration at the very end. The fade-out uses `areverse,
    afade=t=in,areverse` rather than computing an absolute start timestamp
    (`afade=t=out:st=<time>`) -- deliberately: the exact final duration
    depends on how much the trims above actually removed, which isn't known
    without a second probe pass on the intermediate audio; reversing the
    stream, fading *in* from what's now the front (the real end), then
    reversing back again is exact regardless of the real duration, no
    estimate needed. `edge_fade_duration=0` disables both edge fades (same
    "0 to disable" convention `cinemagraph`'s own `--grain` option already
    uses).

    A single track still gets its own edges trimmed and gets edge fades
    even with no join needed.
    """
    if not track_paths:
        raise ValueError("build_music_track needs at least one track.")

    inputs = []
    for path in track_paths:
        inputs += ["-i", path]

    is_gap_mode = gap_duration > 0 and len(track_paths) > 1

    stages = []
    track_labels = []
    for i, _path in enumerate(track_paths):
        filters = [
            f"silenceremove=start_periods=1:start_threshold={_SILENCE_THRESHOLD}:"
            f"start_silence={_SILENCE_MIN_DURATION}:detection=rms",
            f"silenceremove=stop_periods=-1:stop_threshold={_SILENCE_THRESHOLD}:"
            f"stop_silence={_SILENCE_MIN_DURATION}:detection=rms",
        ]
        # In gap mode, each track's edges butt directly against real silence
        # (the anullsrc segment below), not another song's own overlapping
        # content the way acrossfade already blends smoothly in crossfade
        # mode -- so without this, every gap boundary is a hard, audible cut
        # straight to/from silence rather than a graceful pause. Fades every
        # gap-adjacent edge the same duration-independent way the outer
        # edges already do (`edge_fade_duration=0` disables these too, same
        # "0 to disable" convention as everywhere else here).
        if is_gap_mode and edge_fade_duration > 0:
            if i > 0:
                filters.append(f"afade=t=in:st=0:d={edge_fade_duration}")
            if i < len(track_paths) - 1:
                filters.append(
                    f"areverse,afade=t=in:st=0:d={edge_fade_duration},areverse"
                )
        label = f"trimmed{i}"
        stages.append(f"[{i}:a]{','.join(filters)}[{label}]")
        track_labels.append(label)

    if is_gap_mode:
        concat_labels = []
        silence_input_index = len(track_paths)
        for i in range(len(track_paths)):
            concat_labels.append(track_labels[i])
            if i < len(track_paths) - 1:
                inputs += [
                    "-f",
                    "lavfi",
                    "-t",
                    str(gap_duration),
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=44100",
                ]
                concat_labels.append(f"{silence_input_index}:a")
                silence_input_index += 1
        concat_refs = "".join(f"[{label}]" for label in concat_labels)
        stages.append(f"{concat_refs}concat=n={len(concat_labels)}:v=0:a=1[joined]")
        prev_label = "joined"
    elif len(track_paths) == 1:
        prev_label = track_labels[0]
    else:
        prev_label = track_labels[0]
        for i in range(1, len(track_paths)):
            out_label = f"a{i}"
            stages.append(
                f"[{prev_label}][{track_labels[i]}]"
                f"acrossfade=d={crossfade_duration}:curve1=qsin:curve2=qsin[{out_label}]"
            )
            prev_label = out_label

    if edge_fade_duration > 0:
        stages.append(
            f"[{prev_label}]afade=t=in:st=0:d={edge_fade_duration},"
            f"areverse,afade=t=in:st=0:d={edge_fade_duration},areverse[out]"
        )
        final_label = "out"
    else:
        final_label = prev_label

    run_ffmpeg(
        [
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(stages),
            "-map",
            f"[{final_label}]",
            output_path,
        ]
    )


def layer_sound_effects(
    base_audio_path: str,
    effect_paths: list[str],
    output_path: str,
    *,
    run_ffmpeg=ffmpeg_runner.run_ffmpeg,
    probe_duration=ffmpeg_runner.probe_duration,
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

    run_ffmpeg(
        [
            "-y",
            *inputs,
            "-filter_complex",
            f"amix=inputs={len(effect_paths) + 1}:duration=first:dropout_transition=0[a]",
            "-map",
            "[a]",
            output_path,
        ]
    )
