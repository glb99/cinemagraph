"""Shared helpers used by more than one router in server/routers/."""

import asyncio
import shutil
from pathlib import Path

from fastapi import HTTPException, UploadFile

import asset_library as library

from .config import Settings
from .schemas import AssetResponse


def _job_dir(settings: Settings, job_id: str) -> Path:
    job_dir = settings.data_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def _save_upload_sync(upload: UploadFile, dest: Path) -> None:
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)


async def _save_upload(upload: UploadFile, dest: Path) -> None:
    """Copies an upload to disk without blocking the event loop.

    shutil.copyfileobj is synchronous I/O; offloading it to a thread is what
    makes it safe to await from inside an async path operation instead of
    running blocking work directly on the loop.
    """
    await asyncio.to_thread(_save_upload_sync, upload, dest)


async def _resolve_optional_audio_input(
    upload: UploadFile | None,
    asset_id: str | None,
    job_dir: Path,
    field_name: str,
) -> Path | None:
    """Generalizes /render/photo's input_file-xor-input_asset_id pattern to
    an *optional* pair (neither given is valid here, unlike /render/photo's
    input which requires exactly one) -- /generate/music needs this twice
    (source audio, reference audio), both genuinely optional on their own.
    """
    if upload is not None and asset_id is not None:
        raise HTTPException(
            422, f"Supply at most one of `{field_name}_file`/`{field_name}_asset_id`."
        )
    if asset_id is not None:
        asset = library.get(asset_id)
        if asset is None:
            raise HTTPException(422, f"No asset with id '{asset_id}'")
        return asset.path
    if upload is not None:
        path = job_dir / (upload.filename or f"{field_name}.wav")
        await _save_upload(upload, path)
        return path
    return None


def _asset_to_response(asset) -> AssetResponse:
    project = next(
        (
            t[len(library.PROJECT_TAG_PREFIX) :]
            for t in asset.tags
            if t.startswith(library.PROJECT_TAG_PREFIX)
        ),
        None,
    )
    tags = [t for t in asset.tags if not t.startswith(library.PROJECT_TAG_PREFIX)]
    return AssetResponse(
        id=asset.id,
        kind=asset.kind,
        original_filename=asset.original_filename,
        added_at=asset.added_at,
        tags=tags,
        provenance=asset.provenance,
        project=project,
    )


def _resolve_asset_paths(asset_ids: list[str]) -> list[str]:
    """Resolves each id via asset_library.get(), raising a 422-shaped
    HTTPException naming the first missing one -- same eager-validation-at-
    the-route style /render/photo already uses for unknown effect names,
    rather than letting a bad id surface as an opaque failure deep inside
    the background job."""
    paths = []
    for asset_id in asset_ids:
        asset = library.get(asset_id)
        if asset is None:
            raise HTTPException(422, f"No asset with id '{asset_id}'")
        paths.append(str(asset.path))
    return paths
