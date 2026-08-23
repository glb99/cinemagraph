"""/render/video, /render/photo, /mask-preview -- the CLI's `make`,
`from-photo`, and `mask-preview` commands, reachable over HTTP.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

import asset_library as library
from cinemagraph import effects as effects_pkg
from cinemagraph import pipeline, validation

from .. import jobs as jobs_module
from .. import service
from .._route_helpers import _job_dir, _save_upload
from ..config import SettingsDep
from ..schemas import JobResponse

router = APIRouter(tags=["render"])


@router.post("/render/video", response_model=JobResponse)
async def render_video(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    input_file: Annotated[UploadFile, File()],
    mask: Annotated[UploadFile | None, File()] = None,
    still_frame_index: Annotated[int, Form()] = 0,
    blend_frames: Annotated[int, Form()] = 10,
    auto_trim: Annotated[bool, Form()] = True,
    mask_threshold: Annotated[int, Form()] = 25,
    feather: Annotated[int, Form()] = 21,
    apply_grade: Annotated[bool, Form()] = True,
    grade_strength: Annotated[float, Form()] = 1.0,
    grain: Annotated[float, Form()] = 0.03,
    also_gif: Annotated[bool, Form()] = False,
    loop_duration: Annotated[float | None, Form()] = None,
) -> JobResponse:
    if loop_duration and also_gif:
        raise HTTPException(422, pipeline._LOOP_DURATION_GIF_ERROR)

    job = jobs_module.create_job()
    job_dir = _job_dir(settings, job.id)
    input_path = job_dir / (input_file.filename or "input")
    await _save_upload(input_file, input_path)
    output_path = job_dir / "output.mp4"

    mask_path = None
    if mask is not None:
        mask_path = job_dir / (mask.filename or "mask.png")
        await _save_upload(mask, mask_path)

    background_tasks.add_task(
        service.run_render_job,
        job.id,
        pipeline.save_cinemagraph_video,
        output_path,
        library_kind="generated",
        library_tags=["video"],
        input_path=str(input_path),
        mask_path=str(mask_path) if mask_path else None,
        still_frame_index=still_frame_index,
        blend_frames=blend_frames,
        auto_trim_loop=auto_trim,
        mask_threshold=mask_threshold,
        feather=feather,
        apply_grade=apply_grade,
        grade_strength=grade_strength,
        grain=grain,
        also_gif=also_gif,
        loop_duration=loop_duration,
    )
    return JobResponse(job_id=job.id)


@router.post("/render/photo", response_model=JobResponse)
async def render_photo(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    effect: Annotated[list[str], Form()],
    input_file: Annotated[UploadFile | None, File()] = None,
    input_asset_id: Annotated[str | None, Form()] = None,
    mask: Annotated[UploadFile | None, File()] = None,
    mask_prompt: Annotated[str | None, Form()] = None,
    unmasked_effects: Annotated[list[str] | None, Form()] = None,
    duration: Annotated[float, Form()] = 4.0,
    fps: Annotated[int, Form()] = 30,
    speed: Annotated[float, Form()] = 1.0,
    rain_count: Annotated[int | None, Form()] = None,
    rain_opacity: Annotated[float | None, Form()] = None,
    snow_count: Annotated[int | None, Form()] = None,
    snow_opacity: Annotated[float | None, Form()] = None,
    dust_count: Annotated[int | None, Form()] = None,
    dust_opacity: Annotated[float | None, Form()] = None,
    ripple_amplitude: Annotated[float | None, Form()] = None,
    ripple_wavelength: Annotated[float | None, Form()] = None,
    sway_amplitude: Annotated[float | None, Form()] = None,
    sway_freq: Annotated[float | None, Form()] = None,
    wind_amplitude: Annotated[float | None, Form()] = None,
    wind_gustiness: Annotated[float | None, Form()] = None,
    flicker_strength: Annotated[float | None, Form()] = None,
    smoke_opacity: Annotated[float | None, Form()] = None,
    vapor_opacity: Annotated[float | None, Form()] = None,
    feather: Annotated[int, Form()] = 21,
    apply_grade: Annotated[bool, Form()] = True,
    grade_strength: Annotated[float, Form()] = 1.0,
    grain: Annotated[float, Form()] = 0.03,
    also_gif: Annotated[bool, Form()] = False,
    loop_duration: Annotated[float | None, Form()] = None,
) -> JobResponse:
    """Render a photo cinemagraph.

    Supply exactly one of `input_file` (a fresh upload) or `input_asset_id`
    (an existing library asset's id, resolved via `asset_library.get()` --
    its stored file is used directly, no upload/copy needed). Supply at most
    one of `mask` (a hand-painted mask file, same convention as the CLI's
    --mask) or `mask_prompt` (e.g. "clouds", "sun" -- segments the photo via
    the machine-learning service first and renders with that mask; the job
    errors with a 503-style message if that service isn't
    configured/reachable, rather than silently rendering unmasked).
    `unmasked_effects` opts specific requested effects out of the mask
    entirely, so e.g. snow can animate over the whole photo while flicker
    stays confined to `mask`/`mask_prompt` -- see animate_photo()'s own
    docstring. Per-effect overrides (rain_count, ripple_amplitude, etc.)
    mirror the CLI's per-effect flags one-for-one and go through the same
    validation.resolve_effect_kwargs check, so an override for an effect
    that wasn't requested is a 422 here exactly as it's a click.UsageError
    on the CLI.
    """
    unknown = [e for e in effect if e not in effects_pkg.EFFECTS]
    if unknown:
        raise HTTPException(
            422,
            f"Unknown effect(s) {unknown}. Choose from: {', '.join(effects_pkg.EFFECTS)}",
        )
    if mask is not None and mask_prompt:
        raise HTTPException(422, "Supply either `mask` or `mask_prompt`, not both.")
    if loop_duration and also_gif:
        raise HTTPException(422, pipeline._LOOP_DURATION_GIF_ERROR)
    if (input_file is None) == (input_asset_id is None):
        raise HTTPException(
            422, "Supply exactly one of `input_file` or `input_asset_id`."
        )

    per_effect_options = {
        "rain": {"count": rain_count, "opacity": rain_opacity},
        "snow": {"count": snow_count, "opacity": snow_opacity},
        "dust": {"count": dust_count, "opacity": dust_opacity},
        "ripple": {"amplitude": ripple_amplitude, "wavelength": ripple_wavelength},
        "sway": {"amplitude": sway_amplitude, "freq": sway_freq},
        "wind": {"amplitude": wind_amplitude, "gustiness": wind_gustiness},
        "flicker": {"strength": flicker_strength},
        "smoke": {"opacity": smoke_opacity},
        "vapor": {"opacity": vapor_opacity},
    }
    try:
        effect_kwargs = validation.resolve_effect_kwargs(effect, per_effect_options)
        resolved_unmasked = validation.resolve_unmasked_effects(
            effect, unmasked_effects or []
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

    job = jobs_module.create_job()
    job_dir = _job_dir(settings, job.id)
    if input_asset_id is not None:
        asset = library.get(input_asset_id)
        if asset is None:
            raise HTTPException(422, f"No asset with id '{input_asset_id}'")
        input_path = asset.path
    elif input_file is not None:
        input_path = job_dir / (input_file.filename or "input")
        await _save_upload(input_file, input_path)
    else:  # unreachable -- the check at the top of this route rejects it
        raise HTTPException(
            422, "Supply exactly one of `input_file` or `input_asset_id`."
        )
    output_path = job_dir / "output.mp4"

    render_kwargs = {
        "effect": effect,
        "duration": duration,
        "fps": fps,
        "speed": speed,
        "feather": feather,
        "apply_grade": apply_grade,
        "grade_strength": grade_strength,
        "grain": grain,
        "also_gif": also_gif,
        "effect_kwargs": effect_kwargs,
        "unmasked_effects": resolved_unmasked,
        "loop_duration": loop_duration,
    }

    if mask_prompt:
        background_tasks.add_task(
            service.run_photo_semantic_mask_job,
            job.id,
            output_path,
            settings=settings,
            photo_path=input_path,
            mask_path=job_dir / "mask.png",
            mask_prompt=mask_prompt,
            library_kind="generated",
            library_tags=["photo", *effect],
            **render_kwargs,
        )
    else:
        mask_path = None
        if mask is not None:
            mask_path = job_dir / (mask.filename or "mask.png")
            await _save_upload(mask, mask_path)
        background_tasks.add_task(
            service.run_render_job,
            job.id,
            pipeline.save_cinemagraph_from_photo,
            output_path,
            library_kind="generated",
            library_tags=["photo", *effect],
            photo_path=str(input_path),
            mask_path=str(mask_path) if mask_path else None,
            **render_kwargs,
        )
    return JobResponse(job_id=job.id)


@router.post("/mask-preview", response_model=JobResponse)
async def mask_preview(
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    input_file: Annotated[UploadFile, File()],
    mask_threshold: Annotated[int, Form()] = 25,
    feather: Annotated[int, Form()] = 21,
) -> JobResponse:
    """Preview the auto-detected motion mask for a video clip as a PNG,
    without rendering the full cinemagraph -- the API equivalent of
    `cinemagraph mask-preview`. Useful for checking/tuning mask_threshold
    before committing to a full /render/video call.
    """
    job = jobs_module.create_job()
    job_dir = _job_dir(settings, job.id)
    input_path = job_dir / (input_file.filename or "input")
    await _save_upload(input_file, input_path)
    output_path = job_dir / "mask.png"

    background_tasks.add_task(
        service.run_render_job,
        job.id,
        pipeline.save_mask_preview,
        output_path,
        input_path=str(input_path),
        mask_threshold=mask_threshold,
        feather=feather,
    )
    return JobResponse(job_id=job.id)
