"""Long-form assembly: combines already-generated resources (cinemagraph
clips, AI-generated music, AI-generated sound effects) into one finished
video with smooth transitions -- crossfades between video clips, crossfades
between songs, fade-in/out at the whole piece's edges, sound effects layered
continuously under the music.

Own top-level package, same tier as asset_library/ (docs/DESIGN.md sec 3.2's
scope test: this isn't "animate an existing image/video" -- it's a
cross-cutting capability consuming cinemagraph's output, ACE-Step's output,
and Stable Audio's output, none of which is cinemagraph's own job, the same
reasoning that already moved asset_library out of cinemagraph/, sec 5.1).

This is the first place in the project's rendering path that shells out to
an external process (subprocess + imageio_ffmpeg's bundled ffmpeg binary)
rather than calling numpy/opencv/imageio in-process -- deliberate, not
incidental: crossfading and muxing at the filter-graph level (xfade,
acrossfade, afade, amix, -map) is what ffmpeg is for, and reimplementing it
by decoding finished clips back into numpy frame arrays would be slow,
lossy (re-decode/re-encode every clip twice), and duplicate logic ffmpeg
already has hardened. No new dependency: imageio-ffmpeg is already a core
dependency and bundles a full ffmpeg build. See sec 5.6.

Only `ffmpeg_runner.py` touches subprocess directly -- every other function
in this package takes injected `run_ffmpeg`/`probe_duration` parameters
(default: the real ones), the same DI convention call_service/render_fn/
music_generator already use in server/service.py, so tests can fake the
subprocess boundary entirely.
"""
