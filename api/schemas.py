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


class AssetResponse(BaseModel):
    id: str
    kind: str
    original_filename: str
    added_at: str
    tags: list[str]
    provenance: dict | None = None
