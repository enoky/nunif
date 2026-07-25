"""
Preview support for the VideoDepthAnything depth models.

Both families want a sequence rather than an image, which is why iw3 reports
is_image_supported() == False for them. They can still produce a frame:

- VDA_Stream_*: infer() is already the production call path (iw3 routes
  streaming VDA through bind_single_frame_callback) and the model bootstraps
  its temporal cache from the first frame it sees, replicating it across the
  window. One frame is enough, and each extra frame costs another forward pass.

- VDA_*: infer() is dead code (assert 0). The live path is
  infer_with_normalize() plus flush_with_normalize(), and the model only runs a
  forward once it holds INFER_LEN frames; a flush fills the window by cloning
  the last frame. Feeding the frames that precede the previewed one therefore
  costs the same single forward pass as feeding that frame alone, and gives it
  real temporal context.

Neither matches a conversion exactly. A conversion's temporal state spans the
whole video up to that point, with EMA normalisation across frames and resets
at scene boundaries, so treat this as a close approximation rather than the
byte-exact preview the image models give.
"""
import torch
from torchvision.transforms import functional as TF, InterpolationMode


STREAMING_MODEL_NAME = "VideoDepthAnythingStreaming"
ONLINE_MODEL_NAME = "VideoDepthAnything"

# INFER_LEN in video_depth_anything/video_depth_online_torch.py. A different
# value upstream only degrades gracefully: too few frames get padded by the
# flush, too many run a second window.
WINDOW_FRAMES = 32

# the model resizes its input to this short side, so warmup frames are brought
# down to it before being held in VRAM (see warmup_frame_size)
DEFAULT_PREP_LOWER_BOUND = 392
PREP_MULTIPLE = 14

ONLINE_NOTE = ("VideoDepthAnything depth is approximated from a short window of frames, "
               "so it will not match a conversion exactly.")
STREAMING_NOTE = ("VideoDepthAnythingStreaming depth is bootstrapped from this frame alone, "
                  "so it will not match a conversion exactly.")


def model_name(depth_model):
    try:
        return depth_model.get_name()
    except Exception:  # noqa
        return ""


def is_streaming(depth_model):
    return model_name(depth_model) == STREAMING_MODEL_NAME


def is_online(depth_model):
    return model_name(depth_model) == ONLINE_MODEL_NAME


def is_vda(depth_model):
    return is_streaming(depth_model) or is_online(depth_model)


def preview_note(depth_model):
    if is_online(depth_model):
        return ONLINE_NOTE
    if is_streaming(depth_model):
        return STREAMING_NOTE
    return ""


def warmup_frame_count(depth_model):
    """How many frames before the previewed one to feed the model."""
    if is_online(depth_model):
        # the window runs once it is full, so this is free next to a single frame
        return WINDOW_FRAMES - 1
    # streaming pays a forward pass per frame, and bootstraps from one
    return 0


def warmup_frame_size(args):
    """
    Short side to bring warmup frames down to.

    They only condition the temporal state and are resized to this anyway
    inside the model, so holding them at source resolution would waste VRAM
    (31 4K frames is several GB).
    """
    lower_bound = getattr(args, "resolution", None) or DEFAULT_PREP_LOWER_BOUND
    if lower_bound % PREP_MULTIPLE != 0:
        lower_bound += PREP_MULTIPLE - lower_bound % PREP_MULTIPLE
    return lower_bound


def align_frames(frames):
    """
    Brings every frame in a window to one size.

    batch_preprocess() resizes the whole batch to a single size worked out from
    its dimensions, so a batch of mixed sizes cannot even be stacked. The
    warmup frames were already brought down to the model's short side, and that
    is the size the model resizes to anyway, so the previewed frame is matched
    to them rather than the other way around: matching upwards would put the
    whole window in VRAM at source resolution.
    """
    if len(frames) < 2:
        return frames

    size = frames[0].shape[-2:]
    if all(frame.shape[-2:] == size for frame in frames):
        return frames

    return [
        frame if frame.shape[-2:] == size else
        TF.resize(frame, list(size), interpolation=InterpolationMode.BICUBIC,
                  antialias=True).clamp(0, 1)
        for frame in frames
    ]


class VDAOnlineAdapter():
    """
    Gives VideoDepthAnythingModel the infer() that process_image() expects.

    Everything except infer() is delegated, so the wrapped model stays the one
    the rest of the pipeline sees.
    """

    def __init__(self, depth_model, warmup_frames):
        # bypass __getattr__ for our own attributes
        object.__setattr__(self, "_depth_model", depth_model)
        object.__setattr__(self, "_warmup_frames", list(warmup_frames or []))

    def __getattr__(self, name):
        return getattr(self._depth_model, name)

    def infer(self, x, tta=False, low_vram=False, enable_amp=True,
              edge_dilation=0, depth_aa=False, **kwargs):
        depth_model = self._depth_model
        # start from a known state: a previous render left frames buffered
        depth_model.reset_state()

        frames = [frame.to(x.device) for frame in self._warmup_frames]
        frames.append(x)
        batch = torch.stack(align_frames(frames))

        # no scene boundaries: an empty reset_pts means no mid-window flush
        outputs = depth_model.infer_with_normalize(
            batch, pts=list(range(len(frames))), reset_pts=set(),
            enable_amp=enable_amp, edge_dilation=edge_dilation, depth_aa=depth_aa)
        outputs = list(outputs)
        outputs += list(depth_model.flush_with_normalize(
            enable_amp=enable_amp, edge_dilation=edge_dilation, depth_aa=depth_aa))

        if not outputs:
            raise RuntimeError(f"{depth_model.get_name()} returned no depth for the frame")

        # the flush guarantees one output per input, in order
        return outputs[-1]


def wrap(depth_model, warmup_frames):
    """Returns the object process_image() should be given as the depth model."""
    if is_online(depth_model):
        return VDAOnlineAdapter(depth_model, warmup_frames)
    return depth_model
