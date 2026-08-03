"""Pydantic request/response models for the API layer."""
from pydantic import BaseModel


class JobResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    output_path: str | None = None
    error: str | None = None


class CapabilitiesResponse(BaseModel):
    semantic_mask: bool
    music_generation: bool
    sound_effect_generation: bool
    image_generation: bool
    image_generation_models: list[str] = []
    music_generation_models: list[str] = []
    # Purely additive: distinguishes "not configured at all" (nothing to
    # hint about -- the tab correctly stays hidden) from "configured but not
    # currently reachable" (the env var/key is set, but e.g. the container
    # just isn't running right now) -- lets the web UI show a helpful hint
    # instead of the tab silently vanishing. Keyed by the same names as the
    # bool fields above. See docs/DESIGN.md's "configured-but-unreachable
    # UI hint" note.
    configured: dict[str, bool] = {}


class AssetResponse(BaseModel):
    id: str
    kind: str
    original_filename: str
    added_at: str
    tags: list[str]
    provenance: dict | None = None
