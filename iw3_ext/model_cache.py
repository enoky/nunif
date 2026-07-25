import threading
from iw3.stereo_model_factory import create_stereo_model
from nunif.initializer import gc_collect


def effective_divergence(args):
    # same adjustment iw3_main() applies before create_stereo_model()
    return args.divergence * (2.0 if args.synthetic_view in {"right", "left"} else 1.0)


def side_model_key(args):
    overlap = tuple(args.inpaint_overlap_frames) if args.inpaint_overlap_frames else None
    return (args.method, round(effective_divergence(args), 4), args.inpaint_model, overlap, args.gpu[0])


class ModelCache:
    """
    Keeps the stereo model alive between renders.

    create_stereo_model() reloads a checkpoint from disk on every call, which is
    too slow to repeat on each parameter change. The depth model is not cached
    here: it is shared with the main window through MainFrame.depth_model.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.side_model = None
        self.key = None
        self.has_entry = False

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

    def clear(self):
        with self.lock:
            self.side_model = None
            self.key = None
            self.has_entry = False
        gc_collect()
