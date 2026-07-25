"""
Depth maps rendered outside iw3.

A depth map produced by DepthCrafter, DVD, GemDepth or anything else can be
previewed in place of a depth model. The frames are read from a file rather
than inferred; everything after that is the normal pipeline, so edge dilation,
per frame normalisation, the mapper behind Foreground Scale, convergence and
the stereo synthesis all behave exactly as they do with a model.

iw3 already has a notion of pre-rendered depth: --export writes RGB and depth
side by side with a YAML config that can be fed back in. That path reads 16 bit
PNG sequences, and process_config_video() decides the mapper the same way this
does -- the depth carries no mapper of its own, so args.mapper applies.

Depth videos are decoded through the same path as the RGB source, which handles
10 bit and, importantly, the colour range: a limited range file decoded as full
range would quietly flatten the depth. Measured on a 10 bit HEVC gradient, the
round trip holds values to within 0.004.

Alignment is by frame index, not timestamp, so a frame rate written as 29.97 in
one file and 30 in the other cannot drift apart over a long clip.
"""
import copy
from os import path
import torch
import torch.nn.functional as F
from nunif.utils.image_loader import ImageLoader
from iw3.base_depth_model import BaseDepthModel
from iw3.dilation import dilate_edge, edge_dilation_is_enabled
from iw3.utils import is_video, is_image
from .pipeline import PreviewError


MODEL_TYPE = "DepthFile"
# a depth map whose shape differs from the colour frame by more than this is
# probably not the same framing, and stretching it would bend the geometry
ASPECT_TOLERANCE = 0.01


def depth_source_args(args):
    """
    The colour source's settings, minus the ones that belong to it alone.

    -vf is the user's filter chain for the video being converted, and the
    Start/End range selects part of it; neither applies to the depth file,
    which is indexed frame for frame against the whole source.
    """
    depth_args = copy.copy(args)
    depth_args.vf = ""
    depth_args.start_time = None
    depth_args.end_time = None
    return depth_args


def to_depth(x):
    """A decoded frame as a single channel."""
    if x.ndim == 2:
        x = x.unsqueeze(0)
    if x.shape[0] != 1:
        x = x.mean(dim=0, keepdim=True)
    return x.float()


def aspect_ratio(shape):
    height, width = shape[-2:]
    return width / height if height else 0.0


def load_depth_frame(file_path, index, args, device, cache):
    """Returns (1HW depth tensor on device, note)."""
    if not path.exists(file_path):
        raise PreviewError(f"Depth file not found: {file_path}")

    if path.isdir(file_path):
        files = ImageLoader.listdir(file_path)
        if not files:
            raise PreviewError(f"No depth images in {file_path}")
        if index >= len(files):
            raise PreviewError(
                f"The depth sequence has {len(files)} frames, and frame {index} was asked for")
        depth, _ = BaseDepthModel.load_depth(files[index])
        return to_depth(depth).to(device), ""

    if is_image(file_path):
        depth, _ = BaseDepthModel.load_depth(file_path)
        return to_depth(depth).to(device), ""

    if not is_video(file_path):
        raise PreviewError(f"Unsupported depth file: {path.basename(file_path)}")

    source = cache.get(file_path, depth_source_args(args), device)
    x, _, _ = source.grab_index(index)
    return to_depth(x), ""


class FileDepthModel(BaseDepthModel):
    """
    A depth model that reads its output instead of computing it.

    Subclassing gives it the normalisation and EMA plumbing that
    process_image() expects, so the frame goes through the same steps a model's
    output would.
    """

    def __init__(self):
        super().__init__(MODEL_TYPE)
        self.frame = None
        self.rotate_left = False
        self.rotate_right = False
        self.aspect_mismatch = False

    def set_frame(self, depth, args):
        self.frame = depth
        # preprocess_image() has already rotated the colour frame by the time
        # infer() sees it, so the depth has to turn with it
        self.rotate_left = bool(getattr(args, "rotate_left", False))
        self.rotate_right = bool(getattr(args, "rotate_right", False))
        self.aspect_mismatch = False

    def load_model(self, model_type, resolution=None, device=None, **kwargs):
        # load() puts this on the device and calls eval(); nothing runs it
        return torch.nn.Identity()

    def infer(self, x, tta=False, low_vram=False, enable_amp=True,
              edge_dilation=0, depth_aa=False, **kwargs):
        if self.frame is None:
            raise PreviewError("No depth frame was loaded")

        depth = self.frame.to(x.device)
        if self.rotate_left:
            depth = torch.rot90(depth, 1, (-2, -1))
        elif self.rotate_right:
            depth = torch.rot90(depth, 3, (-2, -1))

        if depth.shape[-2:] != x.shape[-2:]:
            self.aspect_mismatch = (
                abs(aspect_ratio(depth.shape) / aspect_ratio(x.shape) - 1.0) > ASPECT_TOLERANCE
                if aspect_ratio(x.shape) else False)
            depth = F.interpolate(depth.unsqueeze(0), size=tuple(x.shape[-2:]),
                                  mode="bilinear", align_corners=False,
                                  antialias=True).squeeze(0)

        if edge_dilation_is_enabled(edge_dilation):
            depth = dilate_edge(depth.unsqueeze(0), edge_dilation).squeeze(0)

        return depth

    @classmethod
    def get_name(cls):
        return MODEL_TYPE

    @classmethod
    def supported(cls, model_type):
        return model_type == MODEL_TYPE

    @classmethod
    def has_checkpoint_file(cls, model_type):
        return True

    @classmethod
    def get_model_path(cls, model_type):
        return None

    def is_metric(self):
        # external depth maps are disparity like, the same assumption
        # BaseDepthModel.load_depth() makes for exported depth
        return False

    @classmethod
    def force_update(cls):
        pass

    @classmethod
    def multi_gpu_supported(cls, name):
        return False
