import sys
from os import path
from time import time
import wx
import iw3.gui as iw3_gui
from iw3.utils import is_video
from nunif.gui import is_dark_mode, apply_dark_mode, set_icon_ex
from nunif.initializer import gc_collect
from iw3.depth_model_factory import create_depth_model
from . import frame_source, pipeline
from .image_canvas import ImageCanvas, pil_to_wx_image
from .model_cache import ModelCache
from .pipeline import PreviewError
from .render_worker import RenderWorker, RenderRequest
from .locales import T
from .settings_watcher import settings_snapshot
from .video_source import VideoSourceCache
from .view_modes import VIEW_MODES
from .window_state import load_state, save_state, is_valid_size, is_visible_position


PREVIEW_SCALES = (100, 50, 25)
SEEK_TICKS = 1000
POLL_INTERVAL_MS = 400
SAME_AS_MAIN = "Same as main"
EMA_NOTE = "Flicker Reduction works across frames, so a single frame cannot show it."


def _ignore_validation_error(*args, **kwargs):
    pass


def image_capable_depth_models(main_frame):
    """The depth models that can render a still frame (VDA models are video-only)."""
    names = []
    for name in main_frame.get_depth_models():
        try:
            if create_depth_model(name).is_image_supported():
                names.append(name)
        except Exception:  # noqa
            continue
    return names


def format_time(seconds):
    if seconds is None:
        return "--:--:--"
    seconds = max(0.0, float(seconds))
    return f"{int(seconds // 3600):02d}:{int((seconds % 3600) // 60):02d}:{seconds % 60:04.1f}"


class RenderResult():
    __slots__ = ("image", "elapsed", "note", "info", "args")

    def __init__(self, image, elapsed, note, info, args):
        self.image = image
        self.elapsed = elapsed
        self.note = note
        self.info = info
        self.args = args


class PreviewFrame(wx.Frame):
    def __init__(self, main_frame, on_close=None):
        super().__init__(
            # parent=None on purpose. A child frame is walked by
            # persistent_manager_register_all(), which would pull these controls
            # into the main window's preset files.
            None,
            name="iw3ext-preview",
            title=T("Live Preview"),
            size=(1120, 720),
        )
        self.main_frame = main_frame
        self.on_close_callback = on_close
        self.seek_position = 0.0
        self.source_input_path = None
        self.source_start = None
        self.source_end = None
        self.render_seq = 0
        self.pil_image = None
        self.model_cache = ModelCache()
        self.video_cache = VideoSourceCache()
        self.worker = RenderWorker(self.render, self.on_render_done, self.on_render_error)
        self.applied_snapshot = None
        self.pending_snapshot = None
        self.SetMinSize((900, 420))

        self.initialize_component()
        self.restore_window_state()
        if is_dark_mode():
            apply_dark_mode(self)
        set_icon_ex(self, path.join(path.dirname(iw3_gui.__file__), "icon.ico"), self.GetTitle())
        self.update_source_state()

    def initialize_component(self):
        # toolbar
        self.pnl_toolbar = wx.Panel(self)
        self.btn_refresh = wx.Button(self.pnl_toolbar, label=T("Refresh"))
        self.chk_auto = wx.CheckBox(self.pnl_toolbar, label=T("Auto"))
        self.chk_auto.SetToolTip(T("Re-render when a setting changes"))

        self.lbl_view = wx.StaticText(self.pnl_toolbar, label=T("View") + ":")
        self.cbo_view = wx.Choice(self.pnl_toolbar, choices=[T(label) for _, label in VIEW_MODES])
        self.cbo_view.SetSelection(0)

        self.lbl_scale = wx.StaticText(self.pnl_toolbar, label=T("Scale") + ":")
        self.cbo_scale = wx.Choice(self.pnl_toolbar, choices=[f"{scale}%" for scale in PREVIEW_SCALES])
        self.cbo_scale.SetSelection(0)
        self.cbo_scale.SetToolTip(T("Downscale the source frame before processing"))

        self.lbl_depth_model = wx.StaticText(self.pnl_toolbar, label=T("Depth") + ":")
        self.cbo_depth_model = wx.Choice(
            self.pnl_toolbar,
            choices=[T(SAME_AS_MAIN)] + image_capable_depth_models(self.main_frame))
        self.cbo_depth_model.SetSelection(0)
        self.cbo_depth_model.SetToolTip(
            T("Render the preview with a different depth model than the conversion"))

        self.btn_zoom_fit = wx.Button(self.pnl_toolbar, label=T("Fit"), style=wx.BU_EXACTFIT)
        self.btn_zoom_100 = wx.Button(self.pnl_toolbar, label="100%", style=wx.BU_EXACTFIT)
        self.btn_save = wx.Button(self.pnl_toolbar, label=T("Save Image") + "...")
        self.btn_free_vram = wx.Button(self.pnl_toolbar, label=T("Free VRAM"))

        layout = wx.BoxSizer(wx.HORIZONTAL)
        layout.Add(self.btn_refresh, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.chk_auto, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.AddSpacer(8)
        layout.Add(self.lbl_view, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.cbo_view, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.AddSpacer(8)
        layout.Add(self.lbl_scale, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.cbo_scale, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.AddSpacer(8)
        layout.Add(self.lbl_depth_model, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.cbo_depth_model, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.AddStretchSpacer()
        layout.Add(self.btn_zoom_fit, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.btn_zoom_100, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.btn_save, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.btn_free_vram, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.pnl_toolbar.SetSizer(layout)

        # canvas
        self.canvas = ImageCanvas(self)

        # seek bar (video input only)
        self.pnl_seek = wx.Panel(self)
        self.sld_seek = wx.Slider(self.pnl_seek, value=0, minValue=0, maxValue=SEEK_TICKS)
        self.lbl_seek = wx.StaticText(self.pnl_seek, label=self.seek_label(), style=wx.ALIGN_RIGHT)
        self.lbl_seek.SetMinSize((180, -1))

        layout = wx.BoxSizer(wx.HORIZONTAL)
        layout.Add(self.sld_seek, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.lbl_seek, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.pnl_seek.SetSizer(layout)

        # main layout
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(self.pnl_toolbar, 0, wx.EXPAND | wx.ALL, 4)
        layout.Add(self.canvas, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        layout.Add(self.pnl_seek, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(layout)

        self.CreateStatusBar(2)
        self.SetStatusWidths([-1, 260])

        # bind
        self.btn_refresh.Bind(wx.EVT_BUTTON, self.on_click_btn_refresh)
        self.chk_auto.Bind(wx.EVT_CHECKBOX, self.on_changed_auto)
        self.cbo_view.Bind(wx.EVT_CHOICE, self.on_changed_render_option)
        self.cbo_scale.Bind(wx.EVT_CHOICE, self.on_changed_render_option)
        self.cbo_depth_model.Bind(wx.EVT_CHOICE, self.on_changed_render_option)
        self.btn_zoom_fit.Bind(wx.EVT_BUTTON, self.on_click_btn_zoom_fit)
        self.btn_zoom_100.Bind(wx.EVT_BUTTON, self.on_click_btn_zoom_100)
        self.btn_save.Bind(wx.EVT_BUTTON, self.on_click_btn_save)
        self.btn_free_vram.Bind(wx.EVT_BUTTON, self.on_click_btn_free_vram)
        self.sld_seek.Bind(wx.EVT_SLIDER, self.on_changed_seek)
        self.Bind(wx.EVT_ACTIVATE, self.on_activate)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        # Auto: watch the main window for setting changes
        self.poll_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_poll_timer, self.poll_timer)
        self.poll_timer.Start(POLL_INTERVAL_MS)

        # controls that operate on a rendered image stay disabled until there is one
        self.update_image_controls()

        self.canvas.set_message(T("Press Refresh to render"))

    # state

    @property
    def view_mode(self):
        return VIEW_MODES[self.cbo_view.GetSelection()][0]

    @property
    def preview_scale(self):
        return PREVIEW_SCALES[self.cbo_scale.GetSelection()]

    @property
    def auto_refresh(self):
        return self.chk_auto.GetValue()

    @property
    def depth_model_override(self):
        selection = self.cbo_depth_model.GetSelection()
        if selection <= 0:
            return None
        return self.cbo_depth_model.GetString(selection)

    def is_busy(self):
        return self.worker.is_busy()

    def restore_window_state(self):
        state = load_state()

        size = state.get("size")
        if is_valid_size(size):
            self.SetSize(size[0], size[1])
        position = state.get("position")
        if is_visible_position(position):
            self.SetPosition(wx.Point(position[0], position[1]))
        if state.get("maximized"):
            self.Maximize(True)

        self.chk_auto.SetValue(bool(state.get("auto", False)))

        view = state.get("view")
        for index, (mode, _) in enumerate(VIEW_MODES):
            if mode == view:
                self.cbo_view.SetSelection(index)
                break

        scale = state.get("scale")
        if scale in PREVIEW_SCALES:
            self.cbo_scale.SetSelection(PREVIEW_SCALES.index(scale))

        depth_model = state.get("depth_model")
        if depth_model:
            # the model may not be offered any more, in which case keep the default
            index = self.cbo_depth_model.FindString(depth_model)
            if index != wx.NOT_FOUND:
                self.cbo_depth_model.SetSelection(index)

        # Auto being on is not a reason to render as soon as the window opens:
        # seed the snapshot so it only fires once something actually changes
        self.applied_snapshot = self.pending_snapshot = self.take_snapshot()

    def collect_window_state(self):
        state = dict(
            maximized=self.IsMaximized(),
            auto=self.auto_refresh,
            view=self.view_mode,
            scale=self.preview_scale,
            depth_model=self.depth_model_override,
        )
        if not (self.IsMaximized() or self.IsIconized()):
            size = self.GetSize()
            position = self.GetPosition()
            state["size"] = [size.width, size.height]
            state["position"] = [position.x, position.y]
        return state

    def seek_seconds(self):
        if self.source_start is None or self.source_end is None:
            return None
        return self.source_start + (self.source_end - self.source_start) * self.seek_position

    def seek_label(self):
        return f"{format_time(self.seek_seconds())} / {format_time(self.source_end)}"

    def update_image_controls(self):
        enable = self.canvas.has_image()
        self.btn_zoom_fit.Enable(enable)
        self.btn_zoom_100.Enable(enable)
        self.btn_save.Enable(enable)

    def update_source_state(self):
        input_path = self.main_frame.pnl_file.input_path
        if input_path != self.source_input_path:
            # a different file: the timeline is unknown again until it renders
            self.source_input_path = input_path
            self.source_start = None
            self.source_end = None
            self.lbl_seek.SetLabel(self.seek_label())

        title = T("Live Preview")
        if input_path:
            title += " - " + path.basename(path.normpath(input_path))
        if self.GetTitle() != title:
            self.SetTitle(title)

        show_seek = bool(input_path) and is_video(input_path)
        if self.pnl_seek.IsShown() != show_seek:
            self.pnl_seek.Show(show_seek)
            self.Layout()

    def set_status(self, message):
        self.SetStatusText(message, 0)

    def set_info(self, message):
        self.SetStatusText(message, 1)

    # rendering

    def parse_args(self, silent):
        """
        parse_args() reads the widgets, so it has to run on the GUI thread.

        It returns None on an invalid value, after showing a modal dialog. An
        auto-refresh must not do that: a half-typed number would put a dialog on
        screen without anyone asking for a render.
        """
        if not silent:
            return self.main_frame.parse_args()

        self.main_frame.show_validation_error_message = _ignore_validation_error
        try:
            return self.main_frame.parse_args()
        finally:
            del self.main_frame.show_validation_error_message

    def request_render(self, silent=False):
        if self.main_frame.processing:
            self.set_status(T("A conversion is running. Preview is paused until it finishes."))
            return

        args = self.parse_args(silent=silent)
        if args is None:
            # do not retry until something changes
            self.applied_snapshot = self.pending_snapshot = self.take_snapshot()
            self.set_status(T("Check the settings"))
            return

        # keep the preview and the main window from cancelling each other
        args.state["stop_event"] = self.worker.stop_event

        # what the main window would have used, to hand back after the render
        share = dict(
            depth_model=args.state["depth_model"],
            depth_model_type=args.depth_model,
            depth_model_device_id=args.gpu,
            depth_model_height=args.resolution,
            depth_model_limit_resolution=args.limit_resolution,
        )

        override = self.depth_model_override
        if override is not None and override != args.depth_model:
            args.state["depth_model"] = self.model_cache.get_depth_model(
                override, args.gpu, args.resolution, args.limit_resolution)
            args.depth_model = override
        else:
            override = None

        self.applied_snapshot = self.pending_snapshot = self.take_snapshot()
        self.render_seq += 1
        self.worker.submit(RenderRequest(
            seq=self.render_seq,
            args=args,
            input_path=self.main_frame.pnl_file.input_path,
            view_mode=self.view_mode,
            scale=self.preview_scale,
            seek=self.seek_position,
            override=override,
            share=share,
        ))
        self.set_status(T("Rendering") + "...")

    def render(self, request, stop_event):
        """Runs on the worker thread."""
        def status_fn(message):
            wx.CallAfter(self.on_render_status, request, message)

        args = request.args
        source_path = frame_source.resolve_source_path(request.input_path)
        args, note = pipeline.prepare_args(args, request.view_mode)
        x, info = frame_source.load_source_frame(
            source_path, args, args.state["device"],
            scale=request.scale, seek=request.seek, video_cache=self.video_cache)
        if info.is_video and getattr(args, "ema_normalize", False) and not note:
            note = EMA_NOTE

        start_time = time()
        image = pipeline.render(args, x, args.state["depth_model"], self.model_cache,
                                request.view_mode, status_fn=status_fn)
        return RenderResult(image=image, elapsed=time() - start_time, note=note,
                            info=info, args=args)

    def is_stale(self, request):
        """True when the window is gone or a newer request has been submitted."""
        try:
            if not self:
                return True
        except RuntimeError:
            return True
        return request.seq != self.render_seq

    def on_render_status(self, request, message):
        if not self.is_stale(request):
            self.set_status(message)

    def on_render_done(self, request, result):
        if self.is_stale(request):
            return

        self.pil_image = result.image
        self.canvas.set_image(pil_to_wx_image(result.image), keep_view=True)
        self.update_image_controls()

        # hand the depth model to the main window, the same way
        # MainFrame.on_exit_worker() does after a conversion. With an override
        # active this is the main window's own model, never the preview's.
        for name, value in request.share.items():
            setattr(self.main_frame, name, value)

        info = result.info
        if info.is_video:
            self.source_start = info.start_time
            self.source_end = info.end_time
            self.lbl_seek.SetLabel(self.seek_label())

        message = T(result.note) if result.note else path.basename(info.file_path)
        if request.override is not None:
            message += f"  [{request.override}]"
        self.set_status(message)
        self.set_info(f"{result.image.width}x{result.image.height}  "
                      f"{result.elapsed * 1000:.0f} ms")

    def on_render_error(self, request, error, traceback_text):
        if self.is_stale(request):
            return

        if isinstance(error, PreviewError):
            message = str(error)
        else:
            print(traceback_text, file=sys.stderr)
            message = f"{error.__class__.__name__}: {error}"

        if not self.canvas.has_image():
            self.canvas.set_message(message)
        self.set_status(message)
        self.set_info("")

    # events

    def on_click_btn_refresh(self, event):
        self.request_render()

    def take_snapshot(self):
        try:
            return settings_snapshot(self.main_frame)
        except RuntimeError:
            # the main window is going away
            return None

    def on_poll_timer(self, event):
        if not self.auto_refresh or not self.IsShown() or self.main_frame.processing:
            return

        snapshot = self.take_snapshot()
        if snapshot is None:
            return
        if snapshot != self.pending_snapshot:
            # still being edited, render once it settles
            self.pending_snapshot = snapshot
            return
        if snapshot != self.applied_snapshot:
            self.request_render(silent=True)

    def on_changed_auto(self, event):
        if self.auto_refresh:
            self.request_render()

    def on_changed_render_option(self, event):
        if self.auto_refresh:
            self.request_render()

    def on_changed_seek(self, event):
        self.seek_position = self.sld_seek.GetValue() / SEEK_TICKS
        self.lbl_seek.SetLabel(self.seek_label())
        if self.auto_refresh:
            self.request_render()

    def on_click_btn_zoom_fit(self, event):
        self.canvas.fit_to_window()

    def on_click_btn_zoom_100(self, event):
        self.canvas.zoom_100()

    def on_click_btn_save(self, event):
        if self.pil_image is None:
            return
        default_name = "preview.png"
        input_path = self.main_frame.pnl_file.input_path
        if input_path:
            default_name = path.splitext(path.basename(path.normpath(input_path)))[0] + "_preview.png"

        with wx.FileDialog(self, T("Save Image"), defaultFile=default_name,
                           wildcard="PNG (*.png)|*.png|JPEG (*.jpg)|*.jpg",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            output_path = dlg.GetPath()

        try:
            image = self.pil_image
            if path.splitext(output_path)[-1].lower() in {".jpg", ".jpeg"}:
                image = image.convert("RGB")
            image.save(output_path)
            self.set_status(T("Saved") + f": {output_path}")
        except Exception as e:  # noqa
            self.set_status(f"{e.__class__.__name__}: {e}")

    def on_click_btn_free_vram(self, event):
        if self.is_busy():
            self.set_status(T("Rendering") + "...")
            return
        self.free_models(include_depth_model=True)
        self.set_status(T("Freed the loaded models"))

    def free_models(self, include_depth_model=False):
        self.model_cache.clear()
        if self.worker.is_busy():
            self.video_cache.release()
        else:
            self.video_cache.close()
        if include_depth_model:
            # same fields MainFrame.parse_args() resets when the model changes
            self.main_frame.depth_model = None
            self.main_frame.depth_model_type = None
            self.main_frame.depth_model_device_id = None
            self.main_frame.depth_model_height = None
            self.main_frame.depth_model_limit_resolution = None
        gc_collect()

    def on_activate(self, event):
        if event.GetActive():
            self.update_source_state()
        event.Skip()

    def close_now(self):
        """Close without notifying the main frame (used while it is shutting down)."""
        self.on_close_callback = None
        self.on_close(None)

    def on_close(self, event):
        save_state(self.collect_window_state())
        if self.on_close_callback is not None:
            self.on_close_callback()
            self.on_close_callback = None
        self.poll_timer.Stop()
        self.render_seq += 1  # discard anything still in flight
        # short join: an idle worker exits at once, and a busy one is a daemon
        # thread that only holds references, so waiting on it would just freeze
        # the close
        self.worker.shutdown(timeout=2.0)
        # the depth model belongs to the main window, leave it cached there
        self.free_models()
        self.Destroy()
