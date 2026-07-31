"""Top-level entry point: builds the video track, builds the audio track
(music + sound effects), and muxes the two into one finished file.
"""
import tempfile
from pathlib import Path

from . import audio_track, ffmpeg_runner, video_track


def assemble(
    video_clip_paths: list[str],
    music_track_paths: list[str],
    output_path: str,
    *,
    sound_effect_paths: list[str] | None = None,
    video_crossfade_duration: float = 1.0,
    music_crossfade_duration: float = 2.0,
    music_edge_fade_duration: float = 2.0,
    music_gap_duration: float = 0.0,
    run_ffmpeg=ffmpeg_runner.run_ffmpeg, probe_duration=ffmpeg_runner.probe_duration,
) -> None:
    """Builds the video track (clips concatenated with crossfades) and the
    audio track (music concatenated with crossfades + edge fades, then
    sound effects layered in) independently, then muxes them into
    `output_path` in one final ffmpeg call. If the two tracks end up
    different lengths, the longer is trimmed to the shorter (`-shortest`) --
    simplest, most predictable outcome, no silence-padding or freeze-frame
    extension logic.

    `music_gap_duration > 0` replaces the crossfade between songs with real
    silence of that length instead (mutually exclusive with
    `music_crossfade_duration` -- see `audio_track.build_music_track`'s own
    docstring). Sound effects are layered in as a separate step *after* the
    music track (with or without a gap) is finished, so they keep playing
    continuously straight through any such gap -- only the music pauses.

    Intermediate video/audio track files live in a temp directory, cleaned
    up once the final mux is written (or if any step raises).
    """
    if not video_clip_paths:
        raise ValueError("assemble needs at least one video clip.")
    if not music_track_paths:
        raise ValueError("assemble needs at least one music track.")

    with tempfile.TemporaryDirectory(prefix="cinemagraph-assembly-") as tmp:
        tmp_dir = Path(tmp)
        video_path = tmp_dir / "video.mp4"
        music_path = tmp_dir / "music.mp3"
        audio_path = tmp_dir / "audio.mp3"

        video_track.build_video_track(
            video_clip_paths, str(video_path),
            crossfade_duration=video_crossfade_duration,
            run_ffmpeg=run_ffmpeg, probe_duration=probe_duration,
        )
        audio_track.build_music_track(
            music_track_paths, str(music_path),
            crossfade_duration=music_crossfade_duration,
            edge_fade_duration=music_edge_fade_duration,
            gap_duration=music_gap_duration,
            run_ffmpeg=run_ffmpeg,
        )
        audio_track.layer_sound_effects(
            str(music_path), sound_effect_paths or [], str(audio_path),
            run_ffmpeg=run_ffmpeg, probe_duration=probe_duration,
        )

        run_ffmpeg([
            "-y", "-i", str(video_path), "-i", str(audio_path),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            output_path,
        ])
