import threading
from iw3.depth_model_factory import create_depth_model
from iw3.stereo_model_factory import create_stereo_model, get_mlbw_divergence_level
from nunif.initializer import gc_collect


# create_stereo_model() matches this name before the mlbw_ prefix test, and does
# not look at divergence for it
DIVERGENCE_FREE_METHODS = {"mlbw_l2_inpaint"}


def effective_divergence(args):
    # same adjustment iw3_main() applies before create_stereo_model()
    return args.divergence * (2.0 if args.synthetic_view in {"right", "left"} else 1.0)


def divergence_level(args):
    """
    Which checkpoint the divergence selects, or None when it selects nothing.

    Keying the cache on the raw divergence would reload a checkpoint on every
    nudge of the slider, which is the control most likely to be dragged.
    """
    method = args.method
    if method in DIVERGENCE_FREE_METHODS:
        return None
    if method.startswith("mlbw_") or method.startswith("mask_mlbw_"):
        return get_mlbw_divergence_level(effective_divergence(args))
    return None


def side_model_key(args):
    overlap = tuple(args.inpaint_overlap_frames) if args.inpaint_overlap_frames else None
    return (args.method, divergence_level(args), args.inpaint_model, overlap, args.gpu[0])


class ModelCache:
    """
    Keeps models alive between renders.

    create_stereo_model() reloads a checkpoint from disk on every call, which is
    too slow to repeat on each parameter change.

    The depth model is normally the main window's (MainFrame.depth_model), so
    the two share one copy in VRAM. Only a preview-only override, which the main
    window must not be given, is cached here.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.side_model = None
        self.key = None
        self.has_entry = False
        self.depth_model = None
        self.depth_key = None

    def get_side_model(self, args, status_fn=None):
        key = side_model_key(args)
        with self.lock:
            if self.has_entry and self.key == key:
                # create_stereo_model() returns None for some methods; that is a
                # valid cache entry too
                return self.side_model
            self.side_model = None
            self.has_entry = False
            self.key = None
        gc_collect()

        if status_fn is not None:
            status_fn(f"Loading {args.method}...")
        side_model = create_stereo_model(
            args.method,
            divergence=effective_divergence(args),
            device_id=args.gpu[0],
            inpaint_model=args.inpaint_model,
            overlap_frames=args.inpaint_overlap_frames,
        )
        with self.lock:
            self.side_model = side_model
            self.key = key
            self.has_entry = True
        return side_model

    def get_depth_model(self, model_type, gpu, resolution, limit_resolution):
        """The preview-only depth model. Loaded lazily by pipeline.prepare_models()."""
        key = (model_type, tuple(gpu), resolution, limit_resolution)
        with self.lock:
            if self.depth_model is not None and self.depth_key == key:
                return self.depth_model
            self.depth_model = None
            self.depth_key = None
        gc_collect()

        depth_model = create_depth_model(model_type)
        with self.lock:
            self.depth_model = depth_model
            self.depth_key = key
        return depth_model

    def clear(self):
        with self.lock:
            self.side_model = None
            self.key = None
            self.has_entry = False
            self.depth_model = None
            self.depth_key = None
        gc_collect()
