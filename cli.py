"""CLI for generating lofi-style cinemagraphs from short video clips.

Usage:
    python cli.py make input.mp4 output.mp4
    python cli.py make input.mp4 output.mp4 --mask mask.png --no-grade
    python cli.py mask-preview input.mp4 preview_mask.png
"""
import click

from cinemagraph import io_utils, mask as mask_mod, photo_effects, pipeline


@click.group()
def cli():
    pass


@cli.command()
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--mask", "mask_path", type=click.Path(exists=True), default=None,
              help="Hand-painted mask (white=animated, black=frozen). Omit to auto-detect motion.")
@click.option("--still-frame", "still_frame_index", type=int, default=0,
              help="Index of the frame to use as the frozen background.")
@click.option("--blend-frames", type=int, default=10, help="Frames used to crossfade the loop point.")
@click.option("--auto-trim/--no-auto-trim", default=True, help="Auto-trim clip to its best natural loop point.")
@click.option("--mask-threshold", type=int, default=25, help="Sensitivity of auto motion detection (lower = more sensitive).")
@click.option("--feather", type=int, default=21, help="Mask edge softness (odd pixel radius).")
@click.option("--grade/--no-grade", "apply_grade", default=True, help="Apply the lofi color grade.")
@click.option("--grade-strength", type=float, default=1.0, help="Strength of the lofi grade.")
@click.option("--grain", type=float, default=0.03, help="Film grain amount (0 to disable).")
@click.option("--gif/--no-gif", "also_gif", default=False, help="Also export a .gif alongside the video.")
@click.option("--save-mask", "mask_preview_path", type=click.Path(), default=None,
              help="Save the (auto-generated) mask as a PNG for inspection/editing.")
@click.option("--loop-duration", type=float, default=None,
              help="Stretch the output to this many seconds by repeating the detected loop "
                   "(e.g. 3600 for an hour), instead of rendering unique frames the whole way. "
                   "Not compatible with --gif.")
def make(input_path, output_path, mask_path, still_frame_index, blend_frames, auto_trim,
         mask_threshold, feather, apply_grade, grade_strength, grain, also_gif, mask_preview_path,
         loop_duration):
    """Turn INPUT_PATH into a looping cinemagraph at OUTPUT_PATH."""
    pipeline.make_cinemagraph(
        input_path=input_path,
        output_path=output_path,
        mask_path=mask_path,
        still_frame_index=still_frame_index,
        blend_frames=blend_frames,
        auto_trim_loop=auto_trim,
        mask_threshold=mask_threshold,
        feather=feather,
        apply_grade=apply_grade,
        grade_strength=grade_strength,
        grain=grain,
        also_gif=also_gif,
        mask_preview_path=mask_preview_path,
        loop_duration=loop_duration,
    )
    click.echo(f"Saved cinemagraph to {output_path}")


@cli.command("mask-preview")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--mask-threshold", type=int, default=25)
@click.option("--feather", type=int, default=21)
def mask_preview(input_path, output_path, mask_threshold, feather):
    """Generate and save an auto motion mask for INPUT_PATH without rendering the full video.

    Use this to check/tune the mask before committing to a full render, or as a
    starting point to hand-touch-up in an image editor.
    """
    frames, _ = io_utils.read_frames(input_path)
    soft_mask = mask_mod.auto_motion_mask(frames, threshold=mask_threshold, feather=feather)
    mask_mod.save_mask_preview(soft_mask, output_path)
    click.echo(f"Saved mask preview to {output_path}")


@cli.command("from-photo")
@click.argument("photo_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--effect", "effects", type=click.Choice(photo_effects.EFFECTS), required=True, multiple=True,
              help="Which procedural motion(s) to animate onto the photo. Repeat to combine effects "
                   "(e.g. --effect dust --effect flicker) -- at most one of rain/snow/dust may be combined "
                   "with any number of ripple/sway/flicker/smoke.")
@click.option("--mask", "mask_path", type=click.Path(exists=True), default=None,
              help="Where the effect applies (white=animated, black=frozen). Omit to apply over the whole photo.")
@click.option("--duration", type=float, default=4.0, help="Length of the loop in seconds.")
@click.option("--fps", type=int, default=30, help="Frames per second.")
@click.option("--speed", type=float, default=1.0,
              help="Motion speed multiplier, independent of --duration (1.0 = default rate).")
@click.option("--count", type=int, default=None,
              help="Number of particles for rain/snow/dust (defaults: rain=90, snow=70, dust=45).")
@click.option("--opacity", type=float, default=None,
              help="Blend strength for rain/snow/dust (defaults: rain=0.55, snow=0.85, dust=0.4). "
                   "Raise this if the effect is hard to see, e.g. dust next to a strong smoke/flicker pass.")
@click.option("--feather", type=int, default=21, help="Mask edge softness (odd pixel radius).")
@click.option("--grade/--no-grade", "apply_grade", default=True, help="Apply the lofi color grade.")
@click.option("--grade-strength", type=float, default=1.0, help="Strength of the lofi grade.")
@click.option("--grain", type=float, default=0.03, help="Film grain amount (0 to disable).")
@click.option("--gif/--no-gif", "also_gif", default=False, help="Also export a .gif alongside the video.")
@click.option("--loop-duration", type=float, default=None,
              help="Stretch the output to this many seconds by repeating the --duration loop "
                   "(e.g. 3600 for an hour), instead of rendering unique frames the whole way. "
                   "Not compatible with --gif.")
def from_photo(photo_path, output_path, effects, mask_path, duration, fps, speed, count, opacity, feather,
                apply_grade, grade_strength, grain, also_gif, loop_duration):
    """Animate a single PHOTO_PATH into a looping cinemagraph using one or more procedural effects.

    Effects: rain, snow, dust, ripple, sway, flicker, smoke.
    No source video needed -- motion is generated algorithmically. --speed
    controls how fast the motion moves regardless of --duration, so a 4s and
    a 20s clip of the same --speed look equally fast, just looping more or
    less often.
    """
    particle_effects = [e for e in effects if e in photo_effects.PARTICLE_EFFECTS]
    if len(particle_effects) > 1:
        raise click.UsageError(f"Only one of rain/snow/dust can be combined at a time, got {particle_effects}.")
    if (count is not None or opacity is not None) and not particle_effects:
        raise click.UsageError(f"--count/--opacity only apply to rain/snow/dust, not {list(effects)}.")

    effect_kwargs = {}
    if count is not None or opacity is not None:
        effect_kwargs[particle_effects[0]] = {"count": count, "opacity": opacity}

    pipeline.make_cinemagraph_from_photo(
        photo_path=photo_path,
        output_path=output_path,
        effect=list(effects),
        mask_path=mask_path,
        duration=duration,
        fps=fps,
        speed=speed,
        effect_kwargs=effect_kwargs,
        feather=feather,
        apply_grade=apply_grade,
        grade_strength=grade_strength,
        grain=grain,
        also_gif=also_gif,
        loop_duration=loop_duration,
    )
    click.echo(f"Saved cinemagraph to {output_path}")


if __name__ == "__main__":
    cli()
