"""Pydantic request/response models for the API layer."""

from pydantic import BaseModel


class JobResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    output_path: str | None = None
    error: str | None = None
    # can_save: true once the job has finished and staged a save candidate
    # (server/jobs.py's Job.pending_library) that POST /jobs/{id}/save
    # hasn't consumed yet -- the web UI shows a "Save to library" button
    # exactly when this is true. saved_asset_id is set after a successful
    # save, letting the UI show "already saved" instead of the button again.
    can_save: bool = False
    saved_asset_id: str | None = None


class ServiceStatus(BaseModel):
    """Per-satellite detail behind the capability booleans below.

    The bools answer "can the UI offer this feature". These answer "what is
    actually going on", which is what the Setup tab and `cinemagraph doctor`
    need in order to tell someone what to do about it -- including which env
    var configures it, so the answer doesn't require reading
    docker-compose.yml.
    """

    name: str
    env_var: str
    configured: bool
    reachable: bool
    # The service's own /health body when it answered: `device`,
    # `model_loaded`, and `inflight_seconds` (plus `status: "stuck"` when the
    # busy watchdog has flagged a wedged request). Free to include -- this
    # route already health-checks every service and used to discard the body.
    health: dict | None = None


class CapabilitiesResponse(BaseModel):
    semantic_mask: bool
    music_generation: bool
    sound_effect_generation: bool
    image_generation: bool
    image_generation_models: list[str] = []
    music_generation_models: list[str] = []
    music_remix_models: list[str] = []
    # Distinguishes "not configured at all" from "configured but not
    # currently reachable" (the env var/key is set, but e.g. the container
    # just isn't running right now). The UI shows a different hint for each
    # and a different status dot; it no longer hides the tab in either case,
    # which is what this field originally existed to avoid. Keyed by the same
    # names as the bool fields above. See docs/DESIGN.md's
    # "configured-but-unreachable UI hint" note.
    configured: dict[str, bool] = {}
    # Running version, so a bug report can name one. Empty when the package
    # metadata isn't readable (e.g. running from a source tree that was never
    # installed).
    version: str = ""
    # One entry per optional satellite, in a stable order. Additive: nothing
    # above changes shape, so existing clients are unaffected.
    services: list[ServiceStatus] = []


class AssetResponse(BaseModel):
    id: str
    kind: str
    original_filename: str
    added_at: str
    tags: list[str]
    provenance: dict | None = None
    # Derived from the asset's own project:* tag (see asset_library's module
    # docstring) -- excluded from `tags` above once present here, so it
    # isn't shown twice.
    project: str | None = None


class GeminiKeyRequest(BaseModel):
    """An empty api_key removes the assignment, so a key set here can be
    taken back out from the same place it was added."""

    api_key: str


class SetupWriteResponse(BaseModel):
    env_file: str
    is_set: bool
    # Always true. Named rather than implied because the whole contract of
    # this route is "stored, not yet in effect": get_settings() is cached for
    # the process lifetime by design, so the running API keeps whatever value
    # it started with (see config.py and env_file.py).
    restart_required: bool = True
