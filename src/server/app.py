"""FastAPI routes.

Routes only: parse the request, delegate to service.py, shape the response.
Every multi-step workflow lives in service.py instead -- the router/service
split that's standard practice for FastAPI projects, and which additionally
makes those workflows unit-testable without an HTTP round-trip. See
docs/DESIGN.md's decision log.

This module itself keeps only the routes that don't fit any single
router's grouping (frontend serving, health, capabilities, effects) plus
startup-time adapter registration; everything else lives in routers/,
grouped by concern (render, jobs, library/projects, generation/assemble)
and wired in below via app.include_router(). Note for
tests/test_api_smoke.py: /capabilities and probe_service() deliberately
stay in this module rather than moving to a router, since that test
monkeypatches `server.app.probe_service` directly.

Renders run as FastAPI BackgroundTasks (no Celery/Redis -- this is a
single-process, local, no-auth personal tool) and write into a job-scoped
directory under CINEMAGRAPH_DATA_DIR. Config (that var, plus the three
optional-service URLs below) is a config.Settings instance, resolved once
per process via Depends(get_settings) rather than read from os.environ at
each call site -- see config.py's module docstring for why, and for the
one thing it deliberately does NOT cover (CINEMAGRAPH_LIBRARY_DIR, which
asset_library reads itself).

/render/video and /render/photo (routers/render.py) have full parity with
the CLI's `make` and `from-photo` commands (mask upload, per-effect
overrides via cinemagraph.validation, loop_duration, etc.) -- both entry
points validate through the same functions and reject the same bad
combinations, just translated into a click.UsageError on one side and an
HTTP 422 on the other. /mask-preview is the API equivalent of `cinemagraph
mask-preview`.

/render/photo has one API-only capability beyond that parity: its input can
be an existing library asset (`input_asset_id`, resolved via
`asset_library.get()`) instead of a fresh upload (`input_file`) -- exactly
one of the two, same "pick one or the other" pattern already used for
`mask`/`mask_prompt` on this route. Web-UI-only by design (the CLI already
works with any file path, including a library asset's own stored path).

Routes that talk to optional external services do so via
_external_service.py's shared call_optional_service()/service_available()
helpers, both of which degrade to a clean 503/false rather than erroring
whenever the service isn't configured or isn't reachable, checked fresh on
every request:

- /mask/semantic (routers/generation.py) proxies to the machine-learning/
  CLIPSeg service (ML_SERVICE_URL). POST /render/photo's mask_prompt field
  chains that same segmentation straight into a render.
- /generate/music proxies to an ACE-Step API server (ACESTEP_URL) -- that
  one's a real job-queue API (release_task -> poll query_result -> download
  via /v1/audio), which is why it reuses this app's own job system
  (server/jobs.py, GET /jobs/{id}, GET /jobs/{id}/file) rather than needing new
  status-tracking infrastructure: "poll a remote job queue and download the
  result" turned out to fit the same Job abstraction already built for
  local renders.
- /generate/sound-effect proxies to the sound-effects/ service
  (SOUND_EFFECTS_URL) -- unlike ACE-Step, that service's own /generate call
  is synchronous (one request, one response with the finished audio), but
  it still runs as a background job since generation genuinely takes tens of
  seconds to minutes and the caller shouldn't hold the connection open.
- /generate/image proxies to an ImageGenerator adapter (generation_ports.py/
  generation_adapters.py), defaulting to SDXLAdapter (local SDXL,
  IMAGE_GENERATION_URL) -- same synchronous-call shape as
  /generate/sound-effect. A hosted API (Gemini's native image models) was
  tried first and reverted: new Google AI Studio accounts require a
  non-refundable minimum prepay to use it at all, found only by actually
  trying to generate an image. See docs/experiments/ for the full account,
  and docs/DESIGN.md sec 3.6 for why that history justifies the port/
  registry indirection here rather than the previous inline HTTP call.
  Accepts an optional `reference_image` upload (img2img: generate
  conditioned on a reference photo instead of pure text) plus `strength`
  (how far the result may deviate from it) -- see run_image_job's own
  docstring.
- /assemble combines already-generated library assets (clips, music, sound
  effects) into one finished video via assembly.pipeline.assemble --
  crossfades between clips, crossfades between songs plus edge fades, sound
  effects layered continuously under the music. Not one of the "optional
  external service" routes above (no satellite container involved, no
  service_available() check) -- purely local ffmpeg subprocess work. See
  docs/DESIGN.md sec 5.6.
- /projects (GET/rename/DELETE) and /library/{id}/project manage the
  reserved-prefix "project:*" tag asset_library uses to group content
  (see its own module docstring) -- no new table, just a friendlier surface
  over tag filtering that already existed. POST /jobs/{id}/save also takes
  an optional `project` field, the primary way an asset gets assigned to one
  in the first place (at the moment it's actually kept, not before).
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

import cinemagraph
from cinemagraph import effects as effects_pkg

from ._external_service import probe_service
from .config import Settings, SettingsDep, get_settings
from .generation_adapters import (
    ACEStepAdapter,
    GeminiAdapter,
    Lyria3Adapter,
    SDXLAdapter,
    StableAudioAdapter,
)
from .generation_registry import (
    available_image_generators,
    available_music_generators,
    available_music_remix_generators,
    register_image_generator,
    register_music_generator,
    register_sound_effect_generator,
)
from .routers import generation, jobs, library, render, setup
from .schemas import CapabilitiesResponse, ServiceStatus

app = FastAPI(title="cinemagraph API")

app.include_router(render.router)
app.include_router(jobs.router)
app.include_router(library.library_router)
app.include_router(library.projects_router)
app.include_router(generation.router)
app.include_router(setup.router)

# Populates generation_registry for real (sec 3.6) -- "sdxl"/"acestep" always
# register (their own satellite reachability is still checked fresh per
# request via service_available, same as before); "gemini"/"lyria3" only
# register when GEMINI_API_KEY is actually configured, the same
# "absent -> just don't offer it" degrade every other optional capability
# already follows -- both are hosted APIs behind the same key, not a
# self-hosted GPU service. run_image_job's/run_music_job's own defaults
# (server/service.py) resolve a model name through this same registry -- see
# their docstrings for why that doesn't shadow a test's own Settings(...)
# for real request traffic.
#
# "stable-audio" registers unconditionally too (same as "sdxl"/"acestep") --
# reachability is still checked fresh per request either way; nothing in
# server/service.py looks it up by name yet (no second sound-effect backend
# exists), the same "mechanism present, unconsumed" phase image and music
# generation themselves started in. See generation_registry.py's own docstring.
_settings = get_settings()
register_image_generator("sdxl", SDXLAdapter(_settings))
register_music_generator("acestep", ACEStepAdapter(_settings))
if _settings.gemini_api_key:
    register_image_generator("gemini", GeminiAdapter(_settings))
    register_music_generator("lyria3", Lyria3Adapter(_settings))
register_sound_effect_generator("stable-audio", StableAudioAdapter(_settings))


_FRONTEND_NOT_BUILT_HTML = """<!doctype html>
<title>cinemagraph</title>
<h1>The web UI hasn't been built yet</h1>
<p>The API itself is running fine -- every other route works. This page is the
built <code>frontend/</code> bundle, which isn't where the server expects it:</p>
<pre>{dist_dir}</pre>
<p>Build it with <code>cd frontend &amp;&amp; bun install &amp;&amp; bun run build</code>, or point
<code>CINEMAGRAPH_FRONTEND_DIR</code> at an existing build. For frontend development
run <code>bun run dev</code> instead and use its own server (port 5173), which
proxies API calls back here.</p>
"""


def _frontend_index(settings: Settings) -> Response:
    """The SPA's entry document, or a page explaining how to build it.

    Deliberately not a hard failure: an API-only deployment (or a source
    checkout where nobody has run `bun run build`) still starts and serves
    every other route, exactly like an unconfigured optional service degrades
    to a clear message rather than a 500. The old single-page UI couldn't have
    this problem -- it was a Python string -- so this is the cost of moving to
    a real bundle, paid once, here.
    """
    index_file = settings.frontend_dist_dir / "index.html"
    if not index_file.is_file():
        return HTMLResponse(
            _FRONTEND_NOT_BUILT_HTML.format(dist_dir=settings.frontend_dist_dir),
            status_code=503,
        )
    # no-store: index.html names hash-suffixed asset files, so a cached copy
    # of it keeps pointing at bundles that no longer exist after a rebuild.
    # The assets themselves are content-hashed and cached hard (see below).
    return FileResponse(index_file, headers={"cache-control": "no-store"})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(settings: SettingsDep) -> Response:
    """The web UI: `frontend/`'s built SPA (see that directory's README and
    docs/DESIGN.md sec 5.5). Replaced server/ui.py's inline HTML string in
    2026-08-14 once the UI outgrew a single page.
    """
    return _frontend_index(settings)


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def frontend_spa_root(settings: SettingsDep) -> Response:
    """The SPA's own entry point. `/` redirects here client-side."""
    return _frontend_index(settings)


@app.get("/ui/{spa_path:path}", response_class=HTMLResponse, include_in_schema=False)
async def frontend_spa(spa_path: str, settings: SettingsDep) -> Response:
    """Every client-side route (/ui/library, /ui/music, ...) serves the same
    document -- the router picks the view once it's running -- so a hard
    reload or a shared link works rather than 404ing.

    The catch-all is scoped to /ui rather than mounted at the root precisely
    because this API owns unprefixed top-level paths (/library, /assemble,
    /jobs, ...): a root-level SPA fallback would shadow them, the same
    collision the frontend's own /ui namespace exists to avoid.
    """
    del spa_path  # every path under /ui resolves to the same entry document
    return _frontend_index(settings)


@app.get("/assets/{asset_path:path}", include_in_schema=False)
async def frontend_asset(asset_path: str, settings: SettingsDep) -> FileResponse:
    """Vite emits every bundle under dist/assets with a content hash in its
    name, so these are immutable and cached accordingly.

    Served through a route rather than `app.mount(StaticFiles(...))` because a
    mount resolves its directory once at import time, which would put the
    frontend path back into the "config frozen at import" category config.py
    exists to get out of -- and would make it un-overridable in tests.
    """
    root = settings.frontend_dist_dir / "assets"
    requested = (root / asset_path).resolve()
    # `..` in the URL path must not escape the bundle directory. FastAPI
    # already normalises most of it away, but this is the check that makes
    # that a guarantee rather than a trust in the framework's parsing.
    if not requested.is_relative_to(root) or not requested.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        requested, headers={"cache-control": "public, max-age=31536000, immutable"}
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(settings: SettingsDep) -> CapabilitiesResponse:
    """image_generation/music_generation are each true if *either* their
    self-hosted satellite health-checks (a real, fresh-per-request call, same
    as every other satellite) or Gemini/Lyria3 is registered (no equivalent
    health check exists for a hosted API -- its registration, gated on
    GEMINI_API_KEY at app startup in this module, is the only signal
    available). *_models lists every registered adapter regardless of live
    reachability -- same "checked fresh at actual request time" philosophy as
    the rest of this file: a registered-but-currently-unreachable SDXL/
    ACE-Step still shows up here, and a real generate request against it
    still degrades to a clear per-job error, exactly as before either
    adapter existed.

    `configured` distinguishes "nothing to hint about" from "operator
    likely just forgot to start a container": a satellite whose env var is
    set but whose health check currently fails is `configured=True,
    <bool>=False` -- the web UI uses that combination to show a hint instead
    of hiding the tab outright (see frontend/src/lib/tabs.ts).
    image_generation's/
    music_generation's own `configured` is true if either backend has *any*
    config present (URL or key), independent of live reachability --
    deliberately not narrowed to "SDXL/ACE-Step only", since a Gemini key
    alone is enough to make either capability genuinely configured even with
    no self-hosted URL at all.

    `music_remix_models` is a subset of `music_generation_models` -- only
    adapters whose `remix()` actually does something (ACEStepAdapter;
    Lyria3Adapter's raises NotImplementedError, see its own docstring). The
    web UI's model picker filters to this list whenever a remix is being
    requested, so a caller can't pick a non-remix-capable model through the
    UI -- POST /generate/music still rejects it server-side too, since the
    UI filtering isn't the only way to hit this route.
    """
    # Probe each satellite exactly once and reuse the result for both the
    # capability bool and the per-service detail. This route already made one
    # health call per service; keeping the answer instead of discarding it is
    # free, and it removes the four separate `await service_available(...)`
    # calls that previously had to stay in sync with the `configured` dict
    # below by hand.
    mask_probe = await probe_service(settings.semantic_mask_service)
    music_probe = await probe_service(settings.music_service)
    sfx_probe = await probe_service(settings.sound_effect_service)
    image_probe = await probe_service(settings.image_generation_service)
    has_gemini = bool(settings.gemini_api_key)

    return CapabilitiesResponse(
        semantic_mask=mask_probe.reachable,
        music_generation=music_probe.reachable or has_gemini,
        sound_effect_generation=sfx_probe.reachable,
        image_generation=image_probe.reachable or has_gemini,
        image_generation_models=list(available_image_generators()),
        music_generation_models=list(available_music_generators()),
        music_remix_models=list(available_music_remix_generators()),
        configured={
            "semantic_mask": mask_probe.configured,
            "music_generation": music_probe.configured or has_gemini,
            "sound_effect_generation": sfx_probe.configured,
            "image_generation": image_probe.configured or has_gemini,
        },
        version=cinemagraph.__version__,
        services=[
            ServiceStatus(
                name=service.name,
                env_var=service.env_var,
                configured=probe.configured,
                reachable=probe.reachable,
                health=probe.health,
            )
            for service, probe in (
                (settings.semantic_mask_service, mask_probe),
                (settings.music_service, music_probe),
                (settings.sound_effect_service, sfx_probe),
                (settings.image_generation_service, image_probe),
            )
        ],
    )


@app.get("/effects")
def list_effects() -> dict[str, list[str]]:
    return {"effects": list(effects_pkg.EFFECTS)}
