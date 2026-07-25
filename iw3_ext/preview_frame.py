from os import path
import wx
import iw3.gui as iw3_gui
from iw3.utils import is_video
from nunif.gui import is_dark_mode, apply_dark_mode, set_icon_ex


VIEW_RESULT = "result"
VIEW_LEFT = "left"
VIEW_RIGHT = "right"
VIEW_DEPTH = "depth"
VIEW_MODES = (
    (VIEW_RESULT, "Result"),
    (VIEW_LEFT, "Left Eye"),
    (VIEW_RIGHT, "Right Eye"),
    (VIEW_DEPTH, "Depth"),
)
PREVIEW_SCALES = (100, 50, 25)
SEEK_TICKS = 1000


def T(s):
    return iw3_gui.T(s)


class PreviewCanvas(wx.Panel):
    """Placeholder canvas. Replaced by the zoom/pan canvas in phase 2."""

    def __init__(self, parent):
        super().__init__(parent, style=wx.FULL_REPAINT_ON_RESIZE)
        self.message = ""
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.on_paint)

    def set_message(self, message):
        self.message = message
        self.Refresh()

    def on_paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()
        if self.message:
            dc.SetTextForeground(self.GetForegroundColour())
            width, height = self.GetClientSize()
            text_width, text_height = dc.GetTextExtent(self.message)
            dc.DrawText(self.message, (width - text_width) // 2, (height - text_height) // 2)


class PreviewFrame(wx.Frame):
    def __init__(self, main_frame, on_close=None):
        super().__init__(
            # parent=None on purpose. A child frame is walked by
            # persistent_manager_register_all(), which would pull these controls
            # into the main window's preset files.
            None,
            name="iw3ext-preview",
            title=T("Live Preview"),
            size=(960, 640),
        )
        self.main_frame = main_frame
        self.on_close_callback = on_close
        self.seek_position = 0.0
        self.SetMinSize((640, 400))

        self.initialize_component()
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

        self.btn_zoom_fit = wx.Button(self.pnl_toolbar, label=T("Fit"), style=wx.BU_EXACTFIT)
        self.btn_zoom_100 = wx.Button(self.pnl_toolbar, label="100%", style=wx.BU_EXACTFIT)
        self.btn_save = wx.Button(self.pnl_toolbar, label=T("Save Image") + "...")

        layout = wx.BoxSizer(wx.HORIZONTAL)
        layout.Add(self.btn_refresh, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.chk_auto, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.AddSpacer(8)
        layout.Add(self.lbl_view, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.cbo_view, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.AddSpacer(8)
        layout.Add(self.lbl_scale, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.cbo_scale, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.AddStretchSpacer()
        layout.Add(self.btn_zoom_fit, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.btn_zoom_100, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        layout.Add(self.btn_save, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.pnl_toolbar.SetSizer(layout)

        # canvas
        self.canvas = PreviewCanvas(self)

        # seek bar (video input only)
        self.pnl_seek = wx.Panel(self)
        self.sld_seek = wx.Slider(self.pnl_seek, value=0, minValue=0, maxValue=SEEK_TICKS)
        self.lbl_seek = wx.StaticText(self.pnl_seek, label="0.0%", style=wx.ALIGN_RIGHT)
        self.lbl_seek.SetMinSize((72, -1))

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
        self.SetStatusWidths([-1, 220])

        # bind
        self.btn_refresh.Bind(wx.EVT_BUTTON, self.on_click_btn_refresh)
        self.chk_auto.Bind(wx.EVT_CHECKBOX, self.on_changed_auto)
        self.cbo_view.Bind(wx.EVT_CHOICE, self.on_changed_view)
        self.cbo_scale.Bind(wx.EVT_CHOICE, self.on_changed_scale)
        self.sld_seek.Bind(wx.EVT_SLIDER, self.on_changed_seek)
        self.Bind(wx.EVT_ACTIVATE, self.on_activate)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        # controls that operate on a rendered image stay disabled until there is one
        self.btn_zoom_fit.Disable()
        self.btn_zoom_100.Disable()
        self.btn_save.Disable()

        self.canvas.set_message(T("No preview yet"))
        self.SetStatusText(T("Preview rendering is not implemented yet"))

    @property
    def view_mode(self):
        return VIEW_MODES[self.cbo_view.GetSelection()][0]

    @property
    def preview_scale(self):
        return PREVIEW_SCALES[self.cbo_scale.GetSelection()]

    @property
    def auto_refresh(self):
        return self.chk_auto.GetValue()

    def is_busy(self):
        # no renderer yet
        return False

    def update_source_state(self):
        input_path = self.main_frame.pnl_file.input_path
        title = T("Live Preview")
        if input_path:
            title += " - " + path.basename(path.normpath(input_path))
        if self.GetTitle() != title:
            self.SetTitle(title)

        show_seek = bool(input_path) and is_video(input_path)
        if self.pnl_seek.IsShown() != show_seek:
            self.pnl_seek.Show(show_seek)
            self.Layout()

    def request_render(self):
        self.SetStatusText(T("Preview rendering is not implemented yet"))

    def on_click_btn_refresh(self, event):
        self.request_render()

    def on_changed_auto(self, event):
        if self.auto_refresh:
            self.request_render()

    def on_changed_view(self, event):
        if self.auto_refresh:
            self.request_render()

    def on_changed_scale(self, event):
        if self.auto_refresh:
            self.request_render()

    def on_changed_seek(self, event):
        self.seek_position = self.sld_seek.GetValue() / SEEK_TICKS
        self.lbl_seek.SetLabel(f"{self.seek_position * 100:.1f}%")
        if self.auto_refresh:
            self.request_render()

    def on_activate(self, event):
        if event.GetActive():
            self.update_source_state()
        event.Skip()

    def close_now(self):
        """Close without notifying the main frame (used while it is shutting down)."""
        self.on_close_callback = None
        self.Destroy()

    def on_close(self, event):
        if self.on_close_callback is not None:
            self.on_close_callback()
            self.on_close_callback = None
        self.Destroy()
