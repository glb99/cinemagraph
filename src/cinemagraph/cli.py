"""CLI for generating lofi-style cinemagraphs from short video clips.

Usage:
    cinemagraph make input.mp4 output.mp4
    cinemagraph make input.mp4 output.mp4 --mask mask.png --no-grade
    cinemagraph mask-preview input.mp4 preview_mask.png
"""
import click

from . import effects as effects_pkg, pipeline, validation


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
    pipeline.save_cinemagraph_video(
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
    pipeline.save_mask_preview(input_path, output_path, mask_threshold=mask_threshold, feather=feather)
    click.echo(f"Saved mask preview to {output_path}")


@cli.command("from-photo")
@click.argument("photo_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--effect", "effects", type=click.Choice(effects_pkg.EFFECTS), required=True, multiple=True,
              help="Which procedural motion(s) to animate onto the photo. Repeat to combine effects "
                   "(e.g. --effect dust --effect flicker), in any mix -- ripple/sway/wind/flicker/smoke/vapor "
                   "run first in listed order; rain/snow/dust (if any) always composite on top last.")
@click.option("--mask", "mask_path", type=click.Path(exists=True), default=None,
              help="Where the effect applies (white=animated, black=frozen). Omit to apply over the whole photo.")
@click.option("--duration", type=float, default=4.0, help="Length of the loop in seconds.")
@click.option("--fps", type=int, default=30, help="Frames per second.")
@click.option("--speed", type=float, default=1.0,
              help="Motion speed multiplier, independent of --duration (1.0 = default rate).")
@click.option("--rain-count", type=int, default=None, help="Number of rain streaks (default: 90).")
@click.option("--rain-opacity", type=float, default=None, help="Rain blend strength (default: 0.55).")
@click.option("--snow-count", type=int, default=None, help="Number of snowflakes (default: 70).")
@click.option("--snow-opacity", type=float, default=None, help="Snow blend strength (default: 0.85).")
@click.option("--dust-count", type=int, default=None, help="Number of dust motes (default: 45).")
@click.option("--dust-opacity", type=float, default=None,
              help="Dust blend strength (default: 0.4). Raise this if dust is hard to see next to a "
                   "strong smoke/flicker pass.")
@click.option("--ripple-amplitude", type=float, default=None, help="Ripple displacement in pixels (default: 4.0).")
@click.option("--ripple-wavelength", type=float, default=None, help="Ripple wavelength in pixels (default: 40.0).")
@click.option("--sway-amplitude", type=float, default=None, help="Sway displacement in pixels (default: 6.0).")
@click.option("--sway-freq", type=float, default=None, help="Sway spatial frequency (default: 1.0).")
@click.option("--wind-amplitude", type=float, default=None, help="Wind gust displacement in pixels (default: 8.0).")
@click.option("--wind-gustiness", type=float, default=None,
              help="Wind gust rate multiplier -- higher gusts more often (default: 1.0).")
@click.option("--flicker-strength", type=float, default=None, help="Flicker brightness swing, 0-1+ (default: 0.25).")
@click.option("--smoke-opacity", type=float, default=None, help="Smoke blend strength (default: 0.35).")
@click.option("--vapor-opacity", type=float, default=None, help="Water vapor/steam blend strength (default: 0.22).")
@click.option("--feather", type=int, default=21, help="Mask edge softness (odd pixel radius).")
@click.option("--grade/--no-grade", "apply_grade", default=True, help="Apply the lofi color grade.")
@click.option("--grade-strength", type=float, default=1.0, help="Strength of the lofi grade.")
@click.option("--grain", type=float, default=0.03, help="Film grain amount (0 to disable).")
@click.option("--gif/--no-gif", "also_gif", default=False, help="Also export a .gif alongside the video.")
@click.option("--loop-duration", type=float, default=None,
              help="Stretch the output to this many seconds by repeating the --duration loop "
                   "(e.g. 3600 for an hour), instead of rendering unique frames the whole way. "
                   "Not compatible with --gif.")
def from_photo(photo_path, output_path, effects, mask_path, duration, fps, speed,
                rain_count, rain_opacity, snow_count, snow_opacity, dust_count, dust_opacity,
                ripple_amplitude, ripple_wavelength, sway_amplitude, sway_freq,
                wind_amplitude, wind_gustiness, flicker_strength, smoke_opacity, vapor_opacity,
                feather, apply_grade, grade_strength, grain, also_gif, loop_duration):
    """Animate a single PHOTO_PATH into a looping cinemagraph using one or more procedural effects.

    Effects: rain, snow, dust, ripple, sway, wind, flicker, smoke, vapor.
    No source video needed -- motion is generated algorithmically. --speed
    controls how fast the motion moves regardless of --duration, so a 4s and
    a 20s clip of the same --speed look equally fast, just looping more or
    less often.
    """
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
        effect_kwargs = validation.resolve_effect_kwargs(list(effects), per_effect_options)
    except ValueError as e:
        raise click.UsageError(str(e))

    pipeline.save_cinemagraph_from_photo(
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
