from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("cinemagraph-tool")
except PackageNotFoundError:
    __version__ = "0+unknown"

from .pipeline import (
    render_video_cinemagraph,
    render_photo_cinemagraph,
    render_mask_preview,
    save_cinemagraph_video,
    save_cinemagraph_from_photo,
    save_mask_preview,
)
from .mask import auto_motion_mask, load_mask
from .effects import animate_photo, EFFECTS as effect_names

__all__ = [
    "__version__",
    "render_video_cinemagraph",
    "render_photo_cinemagraph",
    "render_mask_preview",
    "save_cinemagraph_video",
    "save_cinemagraph_from_photo",
    "save_mask_preview",
    "auto_motion_mask",
    "load_mask",
    "animate_photo",
    "effect_names",
]
