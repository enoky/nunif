"""
Single-frame render path, mirroring the model setup in iw3.utils.iw3_main().

This is the file that is coupled to iw3 internals. iw3_main() prepares its models
inline and then goes straight into file I/O, so a preview that wants byte-identical
output has to repeat that preparation. If an upstream update changes the setup in
iw3_main() (iw3/utils.py), repeat the change here.
"""
from iw3.utils import (
    process_image,
    calc_auto_warp_steps,
    resolve_mapper_name,
    to_pil_image,
)
from .view_modes import VIEW_LEFT, VIEW_RIGHT, VIEW_DEPTH


class PreviewError(RuntimeError):
    """An error worth showing to the user as a plain sentence."""


# every flag that changes how the two eyes are combined by postprocess_image()
STEREO_FORMAT_FLAGS = ("vr180", "half_sbs", "tb", "half_tb", "cross_eyed", "rgbd", "half_rgbd")


def prepare_args(args, view_mode):
    """
    Adjusts a copy of the GUI settings for the selected view mode.

    Returns (args, note), where note is a message to show the user when the
    preview cannot honour the configured output format.
    """
    note = ""

    if args.export or args.export_disparity:
        # Export writes depth files, there is no stereo image to show
        args.export = False
        args.export_disparity = False
        args.debug_depth = True
        note = "Export mode has no image output. Showing the depth map."

    if view_mode == VIEW_DEPTH:
        args.debug_depth = True
    elif view_mode in (VIEW_LEFT, VIEW_RIGHT):
        # Render full SBS and split it. Every other stereo format either mixes
        # the two eyes together (anaglyph) or rescales them (half sbs/tb).
        args.debug_depth = False
        args.anaglyph = None
        for name in STEREO_FORMAT_FLAGS:
            setattr(args, name, False)

    return args, note


def prepare_models(args, depth_model, model_cache, status_fn=None):
    """Mirror of iw3_main() up to the point where it starts processing files."""
    if not depth_model.is_image_supported():
        raise PreviewError(
            f"{args.depth_model} is a video-only depth model, "
            f"so it cannot render a single frame. Choose an image-capable depth model."
        )

    if args.warp_steps is None:
        args.warp_steps = calc_auto_warp_steps(
            method=args.method,
            divergence=args.divergence,
            synthetic_view=args.synthetic_view,
        )

    if not depth_model.loaded():
        if status_fn is not None:
            status_fn(f"Loading {args.depth_model}...")
        depth_model.load(gpu=args.gpu, resolution=args.resolution,
                         limit_resolution=args.limit_resolution)

    args.mapper = resolve_mapper_name(
        mapper=args.mapper,
        foreground_scale=args.foreground_scale,
        metric_depth=depth_model.is_metric(),
        mapper_type=args.mapper_type,
    )

    side_model = model_cache.get_side_model(args, status_fn=status_fn)
    if side_model is not None and hasattr(side_model, "set_mode"):
        side_model.set_mode("image")
        side_model.reset()
    if args.state["convergence_model"] is not None:
        args.state["convergence_model"].reset(enable_ema=False)

    # process_image() asserts a buffer size of 1. process_video()/process_images()
    # set up their own EMA state on every run, so this does not affect them.
    depth_model.disable_ema()

    return side_model


def render(args, x, depth_model, model_cache, view_mode, status_fn=None):
    """Runs one frame through the pipeline and returns a PIL image."""
    side_model = prepare_models(args, depth_model, model_cache, status_fn=status_fn)
    if status_fn is not None:
        status_fn("Rendering...")

    output = process_image(x, args, depth_model, side_model)

    if view_mode in (VIEW_LEFT, VIEW_RIGHT):
        half = output.shape[-1] // 2
        output = output[:, :, :half] if view_mode == VIEW_LEFT else output[:, :, half:]

    return to_pil_image(output)
