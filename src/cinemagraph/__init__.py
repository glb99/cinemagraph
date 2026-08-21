from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("cinemagraph")
except PackageNotFoundError:
    __version__ = "0+unknown"

from .effects import EFFECTS as effect_names
from .effects import animate_photo
from .mask import auto_motion_mask, load_mask
from .pipeline import (
    render_mask_preview,
    render_photo_cinemagraph,
    render_video_cinemagraph,
    save_cinemagraph_from_photo,
    save_cinemagraph_video,
    save_mask_preview,
)

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
