from os import path
from torchvision.transforms import functional as TF, InterpolationMode
from nunif.utils.image_loader import ImageLoader
from nunif.utils.pil_io import load_image_simple
from iw3.utils import is_video, is_text, is_yaml
from .pipeline import PreviewError


class SourceInfo():
    """What the window needs to know about the frame that was rendered."""

    __slots__ = ("file_path", "is_video", "position", "start_time", "end_time", "duration", "fps")

    def __init__(self, file_path, is_video=False, position=None,
                 start_time=None, end_time=None, duration=None, fps=None):
        self.file_path = file_path
        self.is_video = is_video
        self.position = position
        self.start_time = start_time
        self.end_time = end_time
        self.duration = duration
        self.fps = fps


def resolve_source_path(input_path):
    """Maps whatever is in the input box to the single file the preview renders."""
    if not input_path:
        raise PreviewError("No input file selected")

    if path.isdir(input_path):
        files = ImageLoader.listdir(input_path)
        if not files:
            raise PreviewError("No image found in the input directory")
        return files[0]

    if is_yaml(input_path):
        raise PreviewError("Preview does not support YAML (Export) input")

    if is_text(input_path):
        for line in open(input_path, mode="r", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                return line
        raise PreviewError("The input file list is empty")

    if not path.exists(input_path):
        raise PreviewError(f"Not found: {input_path}")

    return input_path


def scale_source(x, scale):
    if scale >= 100:
        return x
    height, width = x.shape[-2:]
    new_height = max(2, int(height * scale / 100))
    new_width = max(2, int(width * scale / 100))
    return TF.resize(x, (new_height, new_width),
                     interpolation=InterpolationMode.BICUBIC, antialias=True).clamp(0, 1)


def load_source_frame(file_path, args, device, scale=100, seek=0.0, video_cache=None,
                      warmup=0, warmup_short_side=None):
    """
    Returns (CHW float tensor on device, warmup frames, SourceInfo).

    warmup frames are the ones preceding it, oldest first, for the temporal
    depth models. Image input has none: those models fall back to bootstrapping
    from the single frame.
    """
    if is_video(file_path):
        if video_cache is None:
            raise PreviewError("No video cache available")
        source = video_cache.get(file_path, args, device)
        x, warmup_frames, position = source.grab(
            seek, warmup=warmup, warmup_short_side=warmup_short_side)
        info = SourceInfo(file_path, is_video=True, position=position,
                          start_time=source.start_time, end_time=source.end_time,
                          duration=source.duration, fps=source.fps)
        return scale_source(x, scale), warmup_frames, info

    im, _ = load_image_simple(file_path, color="rgb",
                              exif_transpose=not args.disable_exif_transpose)
    if im is None:
        raise PreviewError(f"Could not load {file_path}")

    x = TF.to_tensor(im).to(device)
    return scale_source(x, scale), [], SourceInfo(file_path)
