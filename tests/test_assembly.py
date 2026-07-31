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

def test_build_music_track_single_track_still_gets_edge_fades():
    run = _FakeRun()
    probe = _fake_probe({"song.mp3": 10.0})

    audio_track.build_music_track(
        ["song.mp3"], "out.mp3",
        edge_fade_duration=2.0, run_ffmpeg=run, probe_duration=probe,
    )

    assert len(run.calls) == 1
    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    assert "afade=t=in:st=0:d=2.0" in filter_complex
    assert "afade=t=out:st=8.0:d=2.0" in filter_complex  # 10 - 2


def test_build_music_track_multiple_tracks_chains_acrossfade_then_edge_fades():
    run = _FakeRun()
    probe = _fake_probe({"a.mp3": 6.0, "b.mp3": 6.0})

    audio_track.build_music_track(
        ["a.mp3", "b.mp3"], "out.mp3",
        crossfade_duration=2.0, edge_fade_duration=1.0, run_ffmpeg=run, probe_duration=probe,
    )

    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    assert "[0:a][1:a]acrossfade=d=2.0[a1]" in filter_complex
    # total = 6 + 6 - 2 = 10; fade-out starts at 10 - 1 = 9
    assert "afade=t=in:st=0:d=1.0" in filter_complex
    assert "afade=t=out:st=9.0:d=1.0" in filter_complex


def test_build_music_track_gap_duration_inserts_silence_instead_of_crossfade():
    run = _FakeRun()
    probe = _fake_probe({"a.mp3": 6.0, "b.mp3": 6.0, "c.mp3": 6.0})

    audio_track.build_music_track(
        ["a.mp3", "b.mp3", "c.mp3"], "out.mp3",
        gap_duration=1.5, edge_fade_duration=0, run_ffmpeg=run, probe_duration=probe,
    )

    assert len(run.calls) == 1
    args = run.calls[0]
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "acrossfade" not in filter_complex
    assert "concat=n=5:v=0:a=1" in filter_complex  # 3 tracks + 2 silence segments
    assert args.count("anullsrc=channel_layout=stereo:sample_rate=44100") == 2
    assert args.count("-stream_loop") == 0  # not to be confused with layer_sound_effects' own looping


def test_build_music_track_gap_duration_extends_edge_fade_out_start():
    run = _FakeRun()
    probe = _fake_probe({"a.mp3": 6.0, "b.mp3": 6.0})

    audio_track.build_music_track(
        ["a.mp3", "b.mp3"], "out.mp3",
        gap_duration=2.0, edge_fade_duration=1.0, run_ffmpeg=run, probe_duration=probe,
    )

    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    # gap adds duration rather than removing it: total = 6 + 6 + 2 = 14; fade-out at 14 - 1 = 13
    assert "afade=t=out:st=13.0:d=1.0" in filter_complex


def test_build_music_track_gap_duration_ignored_for_a_single_track():
    """Nothing to insert a gap between with only one track -- falls back to
    the plain single-track path (copy-through, or edge fade if requested)."""
    run = _FakeRun()
    audio_track.build_music_track(["song.mp3"], "out.mp3", gap_duration=2.0, edge_fade_duration=0, run_ffmpeg=run)

    assert len(run.calls) == 1
    assert "-c" in run.calls[0] and "copy" in run.calls[0]


def test_build_music_track_zero_edge_fade_skips_afade_entirely():
    run = _FakeRun()
    probe = _fake_probe({"a.mp3": 6.0, "b.mp3": 6.0})

    audio_track.build_music_track(
        ["a.mp3", "b.mp3"], "out.mp3",
        crossfade_duration=2.0, edge_fade_duration=0, run_ffmpeg=run, probe_duration=probe,
    )

    filter_complex = run.calls[0][run.calls[0].index("-filter_complex") + 1]
    assert "afade" not in filter_complex
    assert run.calls[0][run.calls[0].index("-map") + 1] == "[a1]"


def test_build_music_track_single_track_zero_edge_fade_just_copies():
    run = _FakeRun()
    audio_track.build_music_track(["song.mp3"], "out.mp3", edge_fade_duration=0, run_ffmpeg=run)

    assert len(run.calls) == 1
    assert "-c" in run.calls[0] and "copy" in run.calls[0]


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
