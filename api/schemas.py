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
