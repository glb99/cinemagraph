"""/mask/semantic, /generate/*, /assemble -- routes that either proxy to an
optional external service or (for /assemble) drive local ffmpeg work over
already-generated library assets. See server/app.py's module docstring for
the full per-route rationale.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from .. import jobs as jobs_module
from .. import service
from .._external_service import call_optional_service
from .._route_helpers import (
    _job_dir,
    _resolve_asset_paths,
    _resolve_optional_audio_input,
    _save_upload,
)
from ..config import SettingsDep
from ..generation_registry import (
    available_image_generators,
    available_music_generators,
    available_music_remix_generators,
)
from ..schemas import JobResponse

router = APIRouter(tags=["generation"])

_MUSIC_TASK_TYPES = ("text2music", "cover", "repaint")


@router.post("/mask/semantic")
async def semantic_mask(
    settings: SettingsDep,
    image: Annotated[UploadFile, File()],
    prompt: Annotated[str, Form()],
) -> Response:
    """Proxies to the machine-learning (CLIPSeg) service. Returns a clean 503,
    not a 500, whenever that service isn't configured or isn't reachable --
    see machine-learning/README.md for the /segment contract this proxies.
    """
    files = {"image": (image.filename, await image.read(), image.content_type)}
    resp = await call_optional_service(
        settings.semantic_mask_service,
        "POST",
        "/segment",
        data={"prompt": prompt},
        files=files,
        timeout=60.0,
    )
    return Response(content=resp.content, media_type="image/png")


@router.post("/generate/music", response_model=JobResponse)
async def generate_music(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    prompt: Annotated[str, Form()] = "",
    lyrics: Annotated[str, Form()] = "",
    duration: Annotated[float, Form()] = 30.0,
    thinking: Annotated[bool, Form()] = True,
    instrumental: Annotated[bool, Form()] = False,
    model: Annotated[str, Form()] = "acestep",
    task_type: Annotated[str, Form()] = "text2music",
    src_audio_file: Annotated[UploadFile | None, File()] = None,
    src_audio_asset_id: Annotated[str | None, Form()] = None,
    reference_audio_file: Annotated[UploadFile | None, File()] = None,
    reference_audio_asset_id: Annotated[str | None, Form()] = None,
    cover_strength: Annotated[float, Form()] = 1.0,
    repainting_start: Annotated[float, Form()] = 0.0,
    repainting_end: Annotated[float, Form()] = -1.0,
) -> JobResponse:
    """Proxies to a registered MusicGenerator adapter (generation_ports.py) --
    `model` picks which one ("acestep" = local ACE-Step, self-hosted GPU
    queue; "lyria3" = Google's hosted Lyria 3, when GEMINI_API_KEY is
    configured). Same degrade-cleanly contract as /mask/semantic: reuses
    this file's own job system rather than adding new status-tracking
    routes -- GET /jobs/{job_id} and /jobs/{job_id}/file already work for
    this without any changes.

    `instrumental`'s exact effect depends on the chosen `model` -- see each
    adapter's own docstring (ACEStepAdapter substitutes a marker that
    reliably forces it; Lyria3Adapter only requests it via a prompt
    instruction, not a guaranteed override).

    `task_type="cover"`/`"repaint"` remix an existing song -- `src_audio_file`
    (a fresh upload) or `src_audio_asset_id` (an existing library asset),
    exactly one, required for either. `lego`/`extract`/`complete` are real
    ACE-Step task types but base-model-only -- rejected here since the
    currently deployed model is acestep-v15-turbo, a deployment fact this
    route can't work around. `reference_audio_file`/`reference_audio_asset_id`
    (style transfer) is independent of `task_type` and always optional.
    `cover_strength` only matters for `cover`; `repainting_start`/
    `repainting_end` only for `repaint` (`repainting_end=-1` means to the end
    of the source audio) -- see ACEStepAdapter.remix()'s own docstring.
    """
    if task_type not in _MUSIC_TASK_TYPES:
        raise HTTPException(
            422,
            f"Unknown or unsupported task_type '{task_type}'. Choose from: {', '.join(_MUSIC_TASK_TYPES)} "
            "(lego/extract/complete are base-model-only, not supported by the current deployment).",
        )
    is_remix = (
        task_type != "text2music"
        or reference_audio_file is not None
        or reference_audio_asset_id is not None
    )
    valid_models = (
        available_music_remix_generators() if is_remix else available_music_generators()
    )
    if model not in valid_models:
        raise HTTPException(
            422,
            f"Unknown model '{model}' for this request. Choose from: {', '.join(valid_models)}",
        )
    if (
        task_type in ("cover", "repaint")
        and src_audio_file is None
        and src_audio_asset_id is None
    ):
        raise HTTPException(
            422,
            f"task_type='{task_type}' needs `src_audio_file` or `src_audio_asset_id`.",
        )

    job = jobs_module.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.mp3"

    src_audio_path = await _resolve_optional_audio_input(
        src_audio_file, src_audio_asset_id, job_dir, "src_audio"
    )
    reference_audio_path = await _resolve_optional_audio_input(
        reference_audio_file, reference_audio_asset_id, job_dir, "reference_audio"
    )

    background_tasks.add_task(
        service.run_music_job,
        job.id,
        output_path,
        settings=settings,
        prompt=prompt,
        lyrics=lyrics,
        duration=duration,
        thinking=thinking,
        instrumental=instrumental,
        model=model,
        task_type=task_type,
        src_audio_path=src_audio_path,
        reference_audio_path=reference_audio_path,
        cover_strength=cover_strength,
        repainting_start=repainting_start,
        repainting_end=repainting_end,
        library_kind="generated",
    )
    return JobResponse(job_id=job.id)


@router.post("/generate/sound-effect", response_model=JobResponse)
async def generate_sound_effect(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    prompt: Annotated[str, Form()],
    duration: Annotated[float, Form()] = 10.0,
) -> JobResponse:
    """Proxies to the sound-effects/ service (Stable Audio Open). Unlike
    ACE-Step, that service answers in one synchronous call -- still run as
    a background job here since generation takes real time and shouldn't
    block the HTTP connection.
    """
    job = jobs_module.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.wav"

    background_tasks.add_task(
        service.run_sound_effect_job,
        job.id,
        output_path,
        settings=settings,
        prompt=prompt,
        duration=duration,
        library_kind="generated",
    )
    return JobResponse(job_id=job.id)


@router.post("/generate/image", response_model=JobResponse)
async def generate_image(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    prompt: Annotated[str, Form()],
    reference_image: Annotated[UploadFile | None, File()] = None,
    strength: Annotated[float, Form()] = 0.6,
    model: Annotated[str, Form()] = "sdxl",
) -> JobResponse:
    """Proxies to a registered ImageGenerator adapter (generation_ports.py) --
    `model` picks which one ("sdxl" = local SDXL/image-generation/, "gemini"
    = Google's hosted API/generation/, when GEMINI_API_KEY is configured).
    Same background-job shape as /generate/sound-effect either way --
    generation takes real time and shouldn't hold the HTTP connection open.

    `reference_image` is optional (img2img mode -- see run_image_job's own
    docstring); `strength` only matters for SDXL. Saved to the job directory
    before scheduling the background task, same as /render/photo's mask
    upload -- the UploadFile itself doesn't survive past this request.
    """
    if model not in available_image_generators():
        raise HTTPException(
            422,
            f"Unknown model '{model}'. Choose from: {', '.join(available_image_generators())}",
        )

    job = jobs_module.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.png"

    reference_image_path = None
    if reference_image is not None:
        reference_image_path = job_dir / (reference_image.filename or "reference.png")
        await _save_upload(reference_image, reference_image_path)

    background_tasks.add_task(
        service.run_image_job,
        job.id,
        output_path,
        settings=settings,
        prompt=prompt,
        reference_image_path=reference_image_path,
        strength=strength,
        model=model,
        library_kind="generated",
    )
    return JobResponse(job_id=job.id)


@router.post("/assemble", response_model=JobResponse)
async def assemble(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    clip_asset_ids: Annotated[list[str], Form()],
    music_asset_ids: Annotated[list[str], Form()],
    sound_effect_asset_ids: Annotated[list[str] | None, Form()] = None,
    video_crossfade_duration: Annotated[float, Form()] = 1.0,
    music_crossfade_duration: Annotated[float, Form()] = 5.0,
    music_edge_fade_duration: Annotated[float, Form()] = 2.0,
    music_gap_duration: Annotated[float, Form()] = 0.0,
) -> JobResponse:
    """Combines already-generated library assets (clips, music, sound
    effects) into one finished video via assembly.pipeline.assemble --
    crossfades between clips, crossfades between songs plus a fade-in/out at
    the whole track's edges, sound effects layered continuously under the
    music. `music_gap_duration > 0` replaces the crossfade between songs
    with real silence instead (mutually exclusive with
    `music_crossfade_duration`) -- sound effects still play continuously
    through it, only the music pauses. See run_assembly_job's and
    audio_track.build_music_track's own docstrings, and docs/DESIGN.md sec
    5.6.

    Every input is a library asset id (not an upload) -- assembly consumes
    "generated resources" that already exist, it doesn't generate anything
    new itself. Runs as a background job purely because ffmpeg's own work
    takes real time, same as every other multi-second operation here --
    no external HTTP call is involved, unlike /generate/music's own
    async job (see run_assembly_job's own async-vs-sync note).
    """
    sound_effect_asset_ids = sound_effect_asset_ids or []
    video_clip_paths = _resolve_asset_paths(clip_asset_ids)
    music_track_paths = _resolve_asset_paths(music_asset_ids)
    sound_effect_paths = (
        _resolve_asset_paths(sound_effect_asset_ids) if sound_effect_asset_ids else None
    )

    job = jobs_module.create_job()
    job_dir = _job_dir(settings, job.id)
    output_path = job_dir / "output.mp4"

    background_tasks.add_task(
        service.run_assembly_job,
        job.id,
        output_path,
        video_clip_paths=video_clip_paths,
        music_track_paths=music_track_paths,
        sound_effect_paths=sound_effect_paths,
        video_crossfade_duration=video_crossfade_duration,
        music_crossfade_duration=music_crossfade_duration,
        music_edge_fade_duration=music_edge_fade_duration,
        music_gap_duration=music_gap_duration,
        library_kind="generated",
        provenance={
            "clip_asset_ids": clip_asset_ids,
            "music_asset_ids": music_asset_ids,
            "sound_effect_asset_ids": sound_effect_asset_ids,
            "video_crossfade_duration": video_crossfade_duration,
            "music_crossfade_duration": music_crossfade_duration,
            "music_edge_fade_duration": music_edge_fade_duration,
            "music_gap_duration": music_gap_duration,
        },
    )
    return JobResponse(job_id=job.id)
