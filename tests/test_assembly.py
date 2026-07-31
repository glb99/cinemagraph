"""Unit tests for src/assembly/ -- long-form assembly (docs/DESIGN.md sec
5.6). Fakes the subprocess boundary (run_ffmpeg/probe_duration, both
injected parameters per assembly's own DI convention) rather than invoking
real ffmpeg -- these assert on the *filter-graph structure* built (pure
Python string/list logic), which is the actual invariant worth locking down
per sec 3.5; whether ffmpeg's own xfade/acrossfade/amix output is correct
was verified manually against the real binary (see docs/experiments/), not
here.
"""
import pytest

from assembly import audio_track, ffmpeg_runner, pipeline, video_track


class _FakeRun:
    """Records every call's args; never touches a real process."""

    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)


def _fake_probe(durations: dict[str, float]):
    def probe(path):
        return durations[path]
    return probe


# ---- ffmpeg_runner ----

def test_run_ffmpeg_raises_with_stderr_on_failure(monkeypatch):
    class _Result:
        returncode = 1
        stderr = "ffmpeg: something broke"

    monkeypatch.setattr(ffmpeg_runner.subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(RuntimeError, match="something broke"):
        ffmpeg_runner.run_ffmpeg(["-y", "-i", "in.mp4", "out.mp4"])


def test_probe_duration_parses_ffmpeg_stderr(monkeypatch):
    class _Result:
        returncode = 1  # ffmpeg -i with no output always exits non-zero
        stderr = "  Duration: 00:01:02.50, start: 0.000000, bitrate: 128 kb/s"

    monkeypatch.setattr(ffmpeg_runner.subprocess, "run", lambda *a, **k: _Result())
    assert ffmpeg_runner.probe_duration("clip.mp4") == 62.5


def test_probe_duration_raises_clearly_when_not_found(monkeypatch):
    class _Result:
        returncode = 1
        stderr = "not a real media file"

    monkeypatch.setattr(ffmpeg_runner.subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(RuntimeError, match="Could not determine duration"):
        ffmpeg_runner.probe_duration("bad.mp4")


# ---- video_track ----

def test_build_video_track_single_clip_copies_through():
    run = _FakeRun()
    video_track.build_video_track(["clip.mp4"], "out.mp4", run_ffmpeg=run)

    assert len(run.calls) == 1
    assert "-c" in run.calls[0] and "copy" in run.calls[0]


def test_build_video_track_chains_xfade_with_correct_offsets():
    run = _FakeRun()
    probe = _fake_probe({"a.mp4": 3.0, "b.mp4": 4.0, "c.mp4": 5.0})

    video_track.build_video_track(
        ["a.mp4", "b.mp4", "c.mp4"], "out.mp4",
        crossfade_duration=1.0, run_ffmpeg=run, probe_duration=probe,
    )

    assert len(run.calls) == 1
    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    # offset 1 = dur(a) - crossfade = 3 - 1 = 2
    # offset 2 = (dur(a) + dur(b) - crossfade) - crossfade = (3+4-1) - 1 = 5
    assert "[0:v][1:v]xfade=transition=fade:duration=1.0:offset=2.0[v1]" in filter_complex
    assert "[v1][2:v]xfade=transition=fade:duration=1.0:offset=5.0[v]" in filter_complex
    assert "-map" in run.calls[0]
    assert "[v]" in run.calls[0]


def test_build_video_track_rejects_empty_clip_list():
    with pytest.raises(ValueError, match="at least one clip"):
        video_track.build_video_track([], "out.mp4", run_ffmpeg=_FakeRun())


# ---- audio_track ----

_TRIM_ARGS = "start_threshold=0.02:start_silence=0.1:detection=rms"
_TRIM_STOP_ARGS = "stop_threshold=0.02:stop_silence=0.1:detection=rms"


def _trim_stage(index: int) -> str:
    """The exact per-track trim filter chain every track always gets now,
    used by several tests below to build expected `-filter_complex` strings
    without repeating the literal string everywhere."""
    return (
        f"[{index}:a]silenceremove=start_periods=1:{_TRIM_ARGS},"
        f"silenceremove=stop_periods=-1:{_TRIM_STOP_ARGS}[trimmed{index}]"
    )


def test_build_music_track_single_track_gets_trimmed_and_edge_faded():
    run = _FakeRun()
    audio_track.build_music_track(["song.mp3"], "out.mp3", edge_fade_duration=2.0, run_ffmpeg=run)

    assert len(run.calls) == 1
    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    stages = filter_complex.split(";")
    assert stages[0] == _trim_stage(0)
    # duration-independent fade-out (areverse/afade-in/areverse) -- see
    # build_music_track's own docstring for why this replaced computing an
    # absolute afade=t=out:st=<time> from an estimated total duration.
    assert stages[1] == (
        "[trimmed0]afade=t=in:st=0:d=2.0,areverse,afade=t=in:st=0:d=2.0,areverse[out]"
    )
    assert run.calls[0][run.calls[0].index("-map") + 1] == "[out]"


def test_build_music_track_multiple_tracks_trims_every_track_then_crossfades_then_edge_fades():
    """Every track -- including the very first and last, not just interior
    joins -- gets its own leading/trailing near-silence trimmed before
    crossfading. Found via a real bug: real generated songs can have several
    seconds of natural fade-out baked into their own tail; left untrimmed on
    the outer edges, that silence extended well past where
    edge_fade_duration's own fade actually started, reading as the music
    stopping abruptly long before the requested edge fade -- not as a
    smooth, controlled fade-out."""
    run = _FakeRun()

    audio_track.build_music_track(
        ["a.mp3", "b.mp3"], "out.mp3",
        crossfade_duration=2.0, edge_fade_duration=1.0, run_ffmpeg=run,
    )

    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    stages = filter_complex.split(";")
    assert stages[0] == _trim_stage(0)
    assert stages[1] == _trim_stage(1)
    assert stages[2] == "[trimmed0][trimmed1]acrossfade=d=2.0:curve1=qsin:curve2=qsin[a1]"
    assert stages[3] == "[a1]afade=t=in:st=0:d=1.0,areverse,afade=t=in:st=0:d=1.0,areverse[out]"


def test_build_music_track_gap_duration_inserts_silence_instead_of_crossfade():
    run = _FakeRun()

    audio_track.build_music_track(
        ["a.mp3", "b.mp3", "c.mp3"], "out.mp3",
        gap_duration=1.5, edge_fade_duration=0, run_ffmpeg=run,
    )

    assert len(run.calls) == 1
    args = run.calls[0]
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "acrossfade" not in filter_complex
    assert "[trimmed0][trimmed1][trimmed2]" not in filter_complex  # gap segments must sit between them
    assert "concat=n=5:v=0:a=1" in filter_complex  # 3 tracks + 2 silence segments
    assert args.count("anullsrc=channel_layout=stereo:sample_rate=44100") == 2
    assert args.count("-stream_loop") == 0  # not to be confused with layer_sound_effects' own looping


def test_build_music_track_gap_duration_fades_into_and_out_of_each_gap():
    """Found via real user feedback: without this, each track's trimmed edge
    butted directly against real silence with no transition -- an audible
    hard cut, unlike crossfade mode where acrossfade itself already blends
    smoothly. Every gap-adjacent edge (not the very first track's own
    leading edge, not the very last track's own trailing edge -- those are
    edge_fade_duration's job on the *whole piece*, applied afterward) now
    gets its own duration-independent fade into/out of the gap's silence."""
    run = _FakeRun()

    audio_track.build_music_track(
        ["a.mp3", "b.mp3", "c.mp3"], "out.mp3",
        gap_duration=3.0, edge_fade_duration=2.0, run_ffmpeg=run,
    )

    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    stages = filter_complex.split(";")

    def trim_filters(index: int) -> str:
        return (
            f"silenceremove=start_periods=1:{_TRIM_ARGS},"
            f"silenceremove=stop_periods=-1:{_TRIM_STOP_ARGS}"
        )

    # track 0 (first): trim, then only a fade-*out* (precedes a gap) -- no
    # fade-in, that's the whole piece's own leading edge_fade_duration fade.
    assert stages[0] == f"[0:a]{trim_filters(0)},areverse,afade=t=in:st=0:d=2.0,areverse[trimmed0]"
    # track 1 (middle): trim, then both a fade-in (follows a gap) and a
    # fade-out (precedes the next gap).
    assert stages[1] == (
        f"[1:a]{trim_filters(1)},afade=t=in:st=0:d=2.0,areverse,afade=t=in:st=0:d=2.0,areverse[trimmed1]"
    )
    # track 2 (last): trim, then only a fade-*in* (follows a gap) -- no
    # fade-out, that's the whole piece's own trailing edge_fade_duration fade.
    assert stages[2] == f"[2:a]{trim_filters(2)},afade=t=in:st=0:d=2.0[trimmed2]"


def test_build_music_track_gap_duration_no_edge_fade_stays_a_hard_cut():
    """edge_fade_duration=0 disables gap-boundary fades too, same "0 to
    disable" convention as everywhere else -- confirms the new fades are
    additive/optional, not forced on."""
    run = _FakeRun()

    audio_track.build_music_track(
        ["a.mp3", "b.mp3"], "out.mp3",
        gap_duration=3.0, edge_fade_duration=0, run_ffmpeg=run,
    )

    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    stages = filter_complex.split(";")
    assert stages[0] == _trim_stage(0)
    assert stages[1] == _trim_stage(1)
    assert "afade" not in filter_complex


def test_build_music_track_gap_duration_still_gets_duration_independent_edge_fade():
    run = _FakeRun()

    audio_track.build_music_track(
        ["a.mp3", "b.mp3"], "out.mp3",
        gap_duration=2.0, edge_fade_duration=1.0, run_ffmpeg=run,
    )

    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    assert "[joined]afade=t=in:st=0:d=1.0,areverse,afade=t=in:st=0:d=1.0,areverse[out]" in filter_complex


def test_build_music_track_gap_duration_ignored_for_a_single_track():
    """Nothing to insert a gap between with only one track -- falls back to
    the plain single-track path (still trimmed, edge-faded if requested)."""
    run = _FakeRun()
    audio_track.build_music_track(["song.mp3"], "out.mp3", gap_duration=2.0, edge_fade_duration=0, run_ffmpeg=run)

    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    assert filter_complex == _trim_stage(0)
    assert run.calls[0][run.calls[0].index("-map") + 1] == "[trimmed0]"


def test_build_music_track_zero_edge_fade_skips_afade_entirely():
    run = _FakeRun()

    audio_track.build_music_track(
        ["a.mp3", "b.mp3"], "out.mp3",
        crossfade_duration=2.0, edge_fade_duration=0, run_ffmpeg=run,
    )

    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    assert "afade" not in filter_complex
    assert run.calls[0][run.calls[0].index("-map") + 1] == "[a1]"


def test_layer_sound_effects_no_effects_copies_through():
    run = _FakeRun()
    audio_track.layer_sound_effects("music.mp3", [], "out.mp3", run_ffmpeg=run)

    assert len(run.calls) == 1
    assert "-c" in run.calls[0] and "copy" in run.calls[0]


def test_layer_sound_effects_loops_effects_to_base_duration_and_mixes():
    run = _FakeRun()
    probe = _fake_probe({"music.mp3": 12.0})

    audio_track.layer_sound_effects(
        "music.mp3", ["rain.mp3", "wind.mp3"], "out.mp3", run_ffmpeg=run, probe_duration=probe,
    )

    args = run.calls[0]
    assert args.count("-stream_loop") == 2
    assert "12.0" in args  # base duration used to cap each looped effect
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "amix=inputs=3" in filter_complex  # base + 2 effects


# ---- pipeline.assemble ----

def test_assemble_runs_video_then_audio_then_final_mux_with_shortest():
    run = _FakeRun()
    probe = _fake_probe({"clip.mp4": 5.0, "song.mp3": 8.0})

    pipeline.assemble(
        video_clip_paths=["clip.mp4"],
        music_track_paths=["song.mp3"],
        output_path="final.mp4",
        run_ffmpeg=run, probe_duration=probe,
    )

    # video track (copy), music track (edge-fade filter), sfx layer (copy,
    # no effects given), final mux -- in that order.
    assert len(run.calls) == 4
    final_call = run.calls[-1]
    assert "-shortest" in final_call
    assert final_call[-1] == "final.mp4"


def test_assemble_rejects_missing_clips_or_tracks():
    with pytest.raises(ValueError, match="video clip"):
        pipeline.assemble([], ["song.mp3"], "out.mp4", run_ffmpeg=_FakeRun())
    with pytest.raises(ValueError, match="music track"):
        pipeline.assemble(["clip.mp4"], [], "out.mp4", run_ffmpeg=_FakeRun())
