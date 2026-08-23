"""/library/* and /projects/* -- thin routes over the asset_library package,
the same functions the `cinemagraph library` CLI subcommands call.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import asset_library as library

from .._route_helpers import _asset_to_response
from ..schemas import AssetResponse

library_router = APIRouter(prefix="/library", tags=["library"])
projects_router = APIRouter(prefix="/projects", tags=["projects"])


@library_router.post("", response_model=AssetResponse)
def library_add(
    upload: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "reference",
    tags: Annotated[str, Form()] = "",
    project: Annotated[str | None, Form()] = None,
) -> AssetResponse:
    if kind not in library.KINDS:
        raise HTTPException(
            422, f"Unknown kind '{kind}'. Choose from: {', '.join(library.KINDS)}"
        )

    with tempfile.NamedTemporaryFile(
        suffix=Path(upload.filename or "").suffix, delete=False
    ) as tmp:
        shutil.copyfileobj(upload.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if project:
            tag_list.append(f"{library.PROJECT_TAG_PREFIX}{project}")
        asset = library.add(
            str(tmp_path), kind=kind, tags=tag_list, original_filename=upload.filename
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    return _asset_to_response(asset)


@library_router.get("", response_model=list[AssetResponse])
def library_list(
    kind: str | None = None, tag: str | None = None, project: str | None = None
) -> list[AssetResponse]:
    return [
        _asset_to_response(a)
        for a in library.list_assets(kind=kind, tag=tag, project=project)
    ]


@library_router.post("/{asset_id}/project", response_model=AssetResponse)
def set_asset_project(
    asset_id: str, project: Annotated[str | None, Form()] = None
) -> AssetResponse:
    """Assigns (or clears, if `project` is empty/omitted) the project an
    existing library asset belongs to -- the Library tab's own way to
    organize/reorganize content after the fact, independent of the
    save-time assignment POST /jobs/{id}/save also offers."""
    try:
        asset = library.set_project(asset_id, project or None)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _asset_to_response(asset)


@library_router.get("/{asset_id}", response_model=AssetResponse)
def library_get(asset_id: str) -> AssetResponse:
    asset = library.get(asset_id)
    if asset is None:
        raise HTTPException(404, "Asset not found")
    return _asset_to_response(asset)


@library_router.get("/{asset_id}/file")
def library_file(asset_id: str) -> FileResponse:
    asset = library.get(asset_id)
    if asset is None:
        raise HTTPException(404, "Asset not found")
    return FileResponse(str(asset.path))


@library_router.delete("/{asset_id}")
def library_remove(asset_id: str) -> dict[str, str]:
    removed = library.remove(asset_id)
    if not removed:
        raise HTTPException(404, "Asset not found")
    return {"removed": asset_id}


@projects_router.get("", response_model=list[str])
def list_projects() -> list[str]:
    return library.list_projects()


@projects_router.post("/rename")
def rename_project(
    old: Annotated[str, Form()], new: Annotated[str, Form()]
) -> dict[str, str | int]:
    count = library.rename_project(old, new)
    return {"renamed": old, "to": new, "count": count}


@projects_router.delete("/{name}")
def delete_project(name: str) -> dict[str, str | int]:
    count = library.delete_project(name)
    return {"deleted": name, "count": count}
