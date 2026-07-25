"""
Converting with a depth map rendered outside iw3.

The preview reads one depth frame at a time, which is easy because it knows
which frame it is asking for. A conversion does not offer that: iw3 calls
depth_model.infer(x) with pixels alone, no frame identity, and with
max_workers above zero those calls run concurrently in a thread pool. Pairing
depth frames to colour frames by call order would therefore be a race, and a
single colour frame skipped by safe_decode would slide the depth out of
register for the rest of the film without raising anything.

The frame's identity is available one level up. Both frame callbacks receive
the pts, and a conversion always sets fps in its config callback, so
FPSFilter has overwritten that pts with the output frame index:

    src_frame.pts = pts               # nunif/utils/video/video_filter/fps.py
    src_frame.time_base = self.output_time_base

That index comes from each frame's own timestamp, not from counting arrivals,
so it survives dropped frames. Wrapping the two binder functions carries it
down to the depth model, which then reads that exact frame from the depth
file. Order stops mattering, and max_workers can stay where the user put it.

The wrapping is the one place this package reaches into iw3's functions rather
than its classes. It is undone when the conversion ends, and compat.py checks
the functions still exist so a rename refuses loudly.
"""
import contextlib
import threading
from os import path
import torch
import iw3.utils as iw3_utils
from nunif.utils.video import VideoMetadata
from . import depth_file
from .pipeline import PreviewError
from .video_source import VideoSource


# frame rates this far apart are treated as different
FPS_TOLERANCE = 0.001
# containers that carry no frame count are estimated, and the estimate rounds
FRAME_COUNT_TOLERANCE = 1


class DepthStream():
    """Depth frames by output frame index, shared across the worker threads."""

    def __init__(self, file_path, args, device):
        self.file_path = file_path
        self.lock = threading.Lock()
        self.source = VideoSource(file_path, depth_file.depth_source_args(args), device)
        self.frames = VideoMetadata.from_file(file_path).get_frames()

    def frame(self, index):
        if self.frames > 0 and index > self.frames + FRAME_COUNT_TOLERANCE:
            # seeking past the end would quietly hand back the last frame
            raise PreviewError(
                f"Frame {index} was asked for and the depth file has {self.frames}")
        with self.lock:
            x, _, _ = self.source.grab_index(index)
        return depth_file.to_depth(x)

    def close(self):
        with self.lock:
            self.source.close()


def check_pairing(input_path, depth_path, args):
    """
    Refuses combinations that cannot be lined up, rather than drifting.

    Everything here is checked before any encoding starts.
    """
    if not path.exists(depth_path):
        raise PreviewError(f"Depth file not found: {depth_path}")

    if args.autocrop:
        raise PreviewError(
            "Auto Crop cannot be used with a depth file: the crop is worked out from the "
            "colour frames and the depth map would not be cropped with them.")

    source = VideoMetadata.from_file(input_path)
    depth = VideoMetadata.from_file(depth_path)

    source_fps = float(source.get_fps())
    depth_fps = float(depth.get_fps())
    if abs(source_fps - depth_fps) > FPS_TOLERANCE:
        raise PreviewError(
            f"The depth file runs at {depth_fps:.3f} fps and the video at {source_fps:.3f} fps. "
            f"They have to match, since frames are paired by index.")

    if args.max_fps and abs(float(args.max_fps) - source_fps) > FPS_TOLERANCE and \
            float(args.max_fps) < source_fps:
        raise PreviewError(
            f"Max FPS is {args.max_fps} and the video runs at {source_fps:.3f}. "
            f"Dropping frames would put the depth file out of step; raise Max FPS.")

    # get_frames() prefers the container's own count and only estimates when it
    # has none; the estimate is ceil(duration * fps), which lands a frame out
    # often enough that an exact comparison would reject sound pairs
    source_frames = source.get_frames()
    depth_frames = depth.get_frames()
    if source_frames > 0 and depth_frames > 0 and abs(source_frames - depth_frames) > FRAME_COUNT_TOLERANCE:
        raise PreviewError(
            f"The depth file has {depth_frames} frames and the video has {source_frames}. "
            f"They have to match.")

    return min(source_frames, depth_frames) if depth_frames > 0 else source_frames


@contextlib.contextmanager
def pts_keyed_depth(depth_model):
    """
    Carries each frame's index from the frame callbacks to the depth model.

    iw3 builds its callbacks inside process_video_full(), so the binders are
    wrapped for the duration of the conversion. Both are restored afterwards,
    including when the conversion raises.
    """
    original_single = iw3_utils.bind_single_frame_callback
    original_batch = iw3_utils.bind_batch_frame_callback

    def single_frame_binder(*args, **kwargs):
        callback = original_single(*args, **kwargs)

        def wrapped(frame):
            # flush passes None; there is no frame to pair with
            depth_model.set_pending(None if frame is None else [frame.pts])
            return callback(frame)

        return wrapped

    def batch_binder(*args, **kwargs):
        callback, preprocess = original_batch(*args, **kwargs)
        # the pts list travels with the object preprocess() returned, so the
        # worker thread reads the batch it was actually given
        pending = {}
        pending_lock = threading.Lock()

        def wrapped_preprocess(x, pts, flush):
            result = preprocess(x, pts, flush)
            if not flush:
                with pending_lock:
                    pending[id(result)] = list(pts)
            return result

        def wrapped_callback(preprocess_args):
            with pending_lock:
                depth_model.set_pending(pending.pop(id(preprocess_args), None))
            return callback(preprocess_args)

        return wrapped_callback, wrapped_preprocess

    iw3_utils.bind_single_frame_callback = single_frame_binder
    iw3_utils.bind_batch_frame_callback = batch_binder
    try:
        yield
    finally:
        iw3_utils.bind_single_frame_callback = original_single
        iw3_utils.bind_batch_frame_callback = original_batch


def build_depth_model(model_cache, stream, args):
    depth_model = model_cache.get_file_depth_model()
    depth_model.set_stream(stream)
    depth_model.set_frame(None, args)
    return depth_model


def frames_for(depth_model, x):
    """The depth frames for the batch currently being processed."""
    indices = depth_model.take_pending()
    if not indices:
        raise PreviewError("A frame arrived without an index; the depth file cannot be paired")

    frames = [depth_model.stream.frame(index) for index in indices]
    if x.ndim == 3:
        return frames[0]
    return torch.stack([frame.to(x.device) for frame in frames])
