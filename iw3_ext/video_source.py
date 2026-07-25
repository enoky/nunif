"""
Single-frame decoding for video input.

This mirrors the decode path iw3 uses for a conversion: the same hwaccel
selection, the same VideoPreprocessor (colorspace handling and -vf filters) and
the same to_tensor(), so the frame handed to the pipeline is the one the
converter would have seen at that timestamp.

Three differences from a full conversion are unavoidable in a still preview:

- iw3 folds rotation and Max Output Size into the ffmpeg filter chain
  (add_preprocess_vf), while process_image() applies them to the tensor.
  Rotation is the same operation either way; when Max Output Size is set, the
  input downscale runs through torch's bicubic instead of ffmpeg's, which can
  differ by a sub-pixel amount.
- Max FPS is not applied. When it is below the source rate the converter drops
  frames, so the previewed frame may be one it would have skipped.
- With Auto Crop on, the crop comes from this frame alone, where a conversion
  derives one crop from an analysis pass over the whole file.
"""
import sys
import threading
from collections import deque
import av
from torchvision.transforms import functional as TF, InterpolationMode
from nunif.utils.video import VideoMetadata, VideoOutputConfig, to_tensor
from nunif.utils.video.color_transform import setup_color_transform
from nunif.utils.video.hwaccel import create_hwaccel, get_compatible_hwaccel
from nunif.utils.video.metadata import parse_time
from nunif.utils.video.processor import safe_decode, fix_frame_color_av17
from nunif.utils.video.video_preprocessor import VideoPreprocessor
from .pipeline import PreviewError


# a keyframe interval this long is already unusual; the cap only exists so a
# broken index cannot spin forever
MAX_SCAN_FRAMES = 900

# how far ahead the decoder will run on rather than seek. Seeking costs a
# re-decode from the previous keyframe, so stepping or scrubbing forwards is
# much cheaper continued than restarted.
FORWARD_CONTINUE_SECONDS = 4.0


def source_key(file_path, args, device):
    return (file_path, args.vf, args.hwaccel, args.disable_software_fallback,
            str(device), args.start_time, args.end_time,
            args.pix_fmt, args.colorspace, args.video_codec, args.video_format)


def resolve_time_range(args, duration):
    """The slider maps over the Start/End time range when one is set."""
    start = parse_time(args.start_time) if args.start_time else 0.0
    end = parse_time(args.end_time) if args.end_time else duration
    start = min(max(start, 0.0), duration)
    end = min(max(end, 0.0), duration)
    if end <= start:
        return 0.0, duration
    return start, end


class VideoSource():
    """
    An open container plus its preprocessor. Not thread safe: it belongs to the
    render worker thread.
    """

    def __init__(self, file_path, args, device):
        self.file_path = file_path
        self.device = device
        self.key = source_key(file_path, args, device)
        self.strict = args.disable_software_fallback
        self.sw_format = VideoMetadata.from_file(file_path)

        hwaccel = get_compatible_hwaccel(
            args.hwaccel, sw_pix_fmt=self.sw_format.format.name, device=device)
        try:
            self.container = self._open(file_path, hwaccel, device)
        except Exception:  # noqa
            # creating the hw device context can fail outright, which is not the
            # per-frame fallback PyAV's allow_software_fallback covers
            if hwaccel is None or self.strict:
                raise
            hwaccel = None
            self.container = self._open(file_path, hwaccel, device)
        self.hwaccel = hwaccel

        if len(self.container.streams.video) == 0:
            self.close()
            raise PreviewError(f"No video stream in {file_path}")

        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"

        # the same colour setup a conversion derives from the encoding settings
        config = VideoOutputConfig(
            fps=None,
            container_format=args.video_format,
            video_codec=args.video_codec,
            pix_fmt=args.pix_fmt,
            colorspace=args.colorspace,
        )
        input_reformat_options, _ = setup_color_transform(self.sw_format, config, device=device)

        self.preprocessor = VideoPreprocessor(
            stream_pix_fmt=self.stream.pix_fmt,
            sw_format=self.sw_format,
            output_colorspace_mode=config.colorspace,
            fps=None,
            vf=args.vf,
            hwaccel=hwaccel,
            device=device,
            input_reformat_options=input_reformat_options,
        )

        _, duration = self.sw_format.guess_frames(return_duration=True)
        self.duration = duration if duration and duration > 0 else 0.0
        self.start_time, self.end_time = resolve_time_range(args, self.duration)
        try:
            self.fps = float(self.sw_format.get_fps())
        except Exception:  # noqa
            self.fps = 0.0

        # where the decoder currently is, and the frames it has just passed, so
        # that a forward scrub can continue instead of seeking
        self.last_pts = None
        self.recent = deque(maxlen=0)
        self.recent_short_side = None

    def _open(self, file_path, hwaccel, device):
        return av.open(
            file_path, mode="r", metadata_errors="ignore",
            hwaccel=create_hwaccel(device=hwaccel, device_id=device.index,
                                   disable_software_fallback=self.strict))

    def grab(self, position, warmup=0, warmup_short_side=None):
        """
        position is 0.0-1.0 over the Start/End range.

        Returns (tensor, warmup_frames, seconds). warmup_frames holds up to
        `warmup` frames preceding the returned one, oldest first, for the
        temporal depth models. They are shrunk to warmup_short_side because
        they only condition the model's state.
        """
        if self.duration <= 0:
            # no usable duration, all that can be done is take the first frame
            target_sec = 0.0
        else:
            target_sec = self.start_time + (self.end_time - self.start_time) * position
            target_sec = min(max(target_sec, 0.0), max(self.duration - 0.001, 0.0))

        x, warmup_frames = self._decode_at(target_sec, warmup, warmup_short_side)
        return x, warmup_frames, target_sec

    def _seek_seconds(self, target_sec, warmup):
        """Start far enough back that `warmup` frames precede the target."""
        if warmup <= 0 or not self.fps:
            return target_sec
        # a little extra: seeking lands on the keyframe at or before this
        return max(0.0, target_sec - (warmup + 2) / self.fps)

    def _prepare_history(self, warmup, short_side):
        """The rolling history of decoded frames, kept for the temporal models."""
        if self.recent.maxlen != warmup or self.recent_short_side != short_side:
            self.recent = deque(maxlen=warmup)
            self.recent_short_side = short_side

    def _can_continue(self, target_pts):
        """True when the target is just ahead of the decoder's position."""
        if self.last_pts is None or self.stream.time_base is None:
            return False
        limit = FORWARD_CONTINUE_SECONDS / self.stream.time_base
        return self.last_pts < target_pts <= self.last_pts + limit

    def _shrink(self, x, short_side):
        if not short_side:
            return x
        height, width = x.shape[-2:]
        current = min(height, width)
        if current <= short_side:
            return x
        scale = short_side / current
        return TF.resize(x, (max(1, int(height * scale)), max(1, int(width * scale))),
                         interpolation=InterpolationMode.BILINEAR, antialias=True)

    def _decode_at(self, target_sec, warmup=0, warmup_short_side=None):
        time_base = self.stream.time_base
        target_pts = int(target_sec / time_base) if time_base else 0
        self._prepare_history(warmup, warmup_short_side)

        if not self._can_continue(target_pts):
            seek_pts = int(self._seek_seconds(target_sec, warmup) / time_base) if time_base else 0
            try:
                self.container.seek(seek_pts, stream=self.stream, backward=True, any_frame=False)
            except Exception:  # noqa
                # unseekable input: fall back to decoding from wherever it is
                pass
            # the history belongs to wherever the decoder used to be
            self.recent.clear()
            self.last_pts = None

        last_frame = None
        scanned = 0
        for packet in self.container.demux([self.stream]):
            for frame in safe_decode(packet, strict=self.strict):
                if frame.pts is None:
                    continue
                frame = fix_frame_color_av17(frame, self.sw_format)
                scanned += 1
                reached = frame.pts >= target_pts
                for out_frame in self.preprocessor.update(frame):
                    last_frame = out_frame
                    self.last_pts = frame.pts
                    if reached:
                        return to_tensor(last_frame, device=self.device), list(self.recent)
                    if warmup > 0:
                        self.recent.append(
                            self._shrink(to_tensor(out_frame, device=self.device), warmup_short_side))
            if scanned > MAX_SCAN_FRAMES:
                print(f"iw3_ext: gave up seeking to {target_sec:.1f}s after {scanned} frames",
                      file=sys.stderr)
                break

        if last_frame is None:
            raise PreviewError(f"Could not decode a frame at {target_sec:.1f}s")
        return to_tensor(last_frame, device=self.device), list(self.recent)

    def close(self):
        container = getattr(self, "container", None)
        if container is not None:
            self.container = None
            try:
                container.close()
            except Exception:  # noqa
                pass


class VideoSourceCache():
    """Keeps one video open so scrubbing does not reopen it on every frame."""

    def __init__(self):
        self.lock = threading.Lock()
        self.source = None

    def get(self, file_path, args, device):
        key = source_key(file_path, args, device)
        with self.lock:
            source = self.source
            if source is not None and source.key == key:
                return source
            self.source = None
        if source is not None:
            source.close()

        source = VideoSource(file_path, args, device)
        with self.lock:
            self.source = source
        return source

    def release(self):
        """
        Drops the cached source without closing it.

        Used when the worker may still be decoding: closing the container out
        from under it would take PyAV down with it. The worker holds the only
        other reference, so the container closes when its render returns.
        """
        with self.lock:
            self.source = None

    def close(self):
        with self.lock:
            source = self.source
            self.source = None
        if source is not None:
            source.close()
