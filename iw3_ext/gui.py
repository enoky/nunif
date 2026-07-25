# iw3.gui is imported first on purpose: it pulls in nunif.pythonw_fix and
# nunif.gui.subprocess_patch, which have to run before anything else.
import iw3.gui as iw3_gui
from os import path
import wx
from wx.lib.delayedresult import startWorker
from . import compat

compat.check_gui_module(iw3_gui)

try:
    from iw3.utils import is_video
    from . import depth_conversion, depth_file
    from .locales import T
    from .model_cache import ModelCache
    from .preview_frame import PreviewFrame, DEPTH_FILE_LABEL, DEPTH_WILDCARD
except ImportError as e:
    raise compat.import_error(e) from e


def find_sizer_index(sizer, window):
    for i, item in enumerate(sizer.GetChildren()):
        if item.GetWindow() is window:
            return i
    return sizer.GetItemCount()


# captured before main() points iw3.gui.MainFrame at the subclass below
BASE_MAIN_FRAME = iw3_gui.MainFrame


class PreviewMainFrame(iw3_gui.MainFrame):
    def __init__(self):
        # iw3's MainFrame.__init__ calls super(MainFrame, self).__init__(...),
        # which looks MainFrame up in iw3.gui's globals at call time. main()
        # points that global here so IW3App.OnInit builds this frame, which
        # would make that super() call resolve to MainFrame itself and raise.
        # Put the real class back while the constructor runs.
        installed = iw3_gui.MainFrame
        iw3_gui.MainFrame = BASE_MAIN_FRAME
        try:
            super().__init__()
        finally:
            iw3_gui.MainFrame = installed

    def initialize_component(self):
        super().initialize_component()
        self.preview_frame = None
        self.conversion_depth_file = None
        # holds the file-backed depth model used by a conversion, separate from
        # the preview's own cache
        self.depth_file_cache = ModelCache()
        self.install_live_preview_button()

    def install_live_preview_button(self):
        compat.check_main_frame(self)
        sizer = self.pnl_process.GetSizer()
        index = find_sizer_index(sizer, self.btn_start)

        self.btn_depth_file = wx.Button(self.pnl_process, label=T(DEPTH_FILE_LABEL),
                                        name="iw3ext_btn_depth_file")
        self.btn_depth_file.SetToolTip(T("Convert using a depth map rendered outside iw3"))
        sizer.Insert(index, self.btn_depth_file, 0, wx.ALL, 4)
        self.btn_depth_file.Bind(wx.EVT_BUTTON, self.on_click_btn_depth_file)

        self.btn_live_preview = wx.Button(self.pnl_process, label=T("Live Preview"),
                                          name="iw3ext_btn_live_preview")
        sizer.Insert(index + 1, self.btn_live_preview, 0, wx.ALL, 4)
        self.btn_live_preview.Bind(wx.EVT_BUTTON, self.on_click_btn_live_preview)

        self.pnl_process.Layout()
        self.update_depth_file_button()
        self.update_start_button_state()

    # conversion with a depth file

    def update_depth_file_button(self):
        if self.conversion_depth_file:
            label = T("Depth") + ": " + path.basename(self.conversion_depth_file)
            self.btn_depth_file.SetToolTip(
                self.conversion_depth_file + "\n" + T("Click to change or clear"))
        else:
            label = T(DEPTH_FILE_LABEL)
            self.btn_depth_file.SetToolTip(T("Convert using a depth map rendered outside iw3"))
        if self.btn_depth_file.GetLabel() != label:
            self.btn_depth_file.SetLabel(label)
            self.pnl_process.Layout()

    def on_click_btn_depth_file(self, event):
        if self.conversion_depth_file:
            with wx.MessageDialog(
                    self,
                    message=(path.basename(self.conversion_depth_file) + "\n\n" +
                             T("Keep using this depth file?")),
                    caption=T("Depth file"),
                    style=wx.YES_NO | wx.CANCEL) as dlg:
                dlg.SetYesNoCancelLabels(T("Choose another"), T("Stop using it"), T("Cancel"))
                answer = dlg.ShowModal()
            if answer == wx.ID_CANCEL:
                return
            if answer == wx.ID_NO:
                self.conversion_depth_file = None
                self.update_depth_file_button()
                return

        with wx.FileDialog(self, T("Depth file"), wildcard=DEPTH_WILDCARD,
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            self.conversion_depth_file = dlg.GetPath()
        self.update_depth_file_button()

    def preview_is_alive(self):
        if self.preview_frame is None:
            return False
        try:
            return bool(self.preview_frame)
        except RuntimeError:
            # the C++ side is already gone
            self.preview_frame = None
            return False

    def update_start_button_state(self):
        super().update_start_button_state()
        # called from MainFrame.initialize_component() before the button exists
        btn = getattr(self, "btn_live_preview", None)
        if btn is None:
            return
        if self.processing or not self.pnl_file.input_path:
            btn.Disable()
        else:
            btn.Enable()

    def on_click_btn_live_preview(self, event):
        if self.preview_is_alive():
            self.preview_frame.Iconize(False)
            self.preview_frame.Raise()
            return
        self.preview_frame = PreviewFrame(self, on_close=self.on_preview_closed)
        self.preview_frame.Show()

    def on_preview_closed(self):
        self.preview_frame = None

    def on_click_btn_start(self, event):
        if self.preview_is_alive() and self.preview_frame.is_busy():
            with wx.MessageDialog(
                    self,
                    message=T("A preview render is still running. Wait for it to finish."),
                    caption=T("Live Preview"),
                    style=wx.OK | wx.ICON_INFORMATION) as dlg:
                dlg.ShowModal()
            return

        if self.conversion_depth_file:
            self.start_with_depth_file(event)
            return

        super().on_click_btn_start(event)

    def start_with_depth_file(self, event):
        """
        Start, with the depth read from a file instead of inferred.

        The checks run before anything is encoded: a pairing that cannot be
        trusted is refused rather than left to drift.
        """
        args = self.parse_args()
        if args is None:
            return
        if not is_video(args.input):
            wx.MessageBox(T("A depth file can only be used with a video input"),
                          T("Depth file"), wx.OK | wx.ICON_ERROR)
            return
        try:
            frames = depth_conversion.check_pairing(args.input, self.conversion_depth_file, args)
        except Exception as e:  # noqa
            wx.MessageBox(str(e), T("Depth file"), wx.OK | wx.ICON_ERROR)
            return

        with wx.MessageDialog(
                self,
                message=(T("Convert with this depth file?") + "\n\n" +
                         path.basename(self.conversion_depth_file) + "\n" +
                         T("Frames") + f": {frames}"),
                caption=T("Depth file"), style=wx.YES_NO) as dlg:
            if dlg.ShowModal() != wx.ID_YES:
                return

        if not self.confirm_overwrite(args):
            return

        stream = depth_conversion.DepthStream(
            self.conversion_depth_file, args, args.state["device"])
        depth_model = depth_conversion.build_depth_model(self.depth_file_cache, stream, args)
        args.state["depth_model"] = depth_model
        args.depth_model = depth_file.MODEL_TYPE

        def run(args):
            try:
                with depth_conversion.pts_keyed_depth(depth_model):
                    return iw3_gui.iw3_main(args)
            finally:
                stream.close()
                depth_model.set_stream(None)

        self.btn_autocrop_test.Disable()
        self.btn_start.Disable()
        self.btn_cancel.Enable()
        self.btn_suspend.Enable()
        self.stop_event.clear()
        self.suspend_event.set()
        self.prg_tqdm.SetValue(0)
        self.SetStatusText(T("Depth") + ": " + path.basename(self.conversion_depth_file))

        startWorker(self.on_exit_worker_depth_file, run, wargs=(args,))
        self.processing = True

    def on_exit_worker_depth_file(self, result):
        # the depth model is this package's, not the main window's; do not let
        # it be cached there as if it were a real one
        try:
            self.on_exit_worker(result)
        finally:
            self.depth_model = None
            self.depth_model_type = None
            self.depth_model_device_id = None
            self.depth_model_height = None
            self.depth_model_limit_resolution = None

    def on_close(self, event):
        if self.preview_is_alive():
            self.preview_frame.close_now()
        self.preview_frame = None
        super().on_close(event)


def main():
    iw3_gui.MainFrame = PreviewMainFrame
    iw3_gui.main()


if __name__ == "__main__":
    from nunif.utils.video import pyav_init_cuda_primary_context
    from nunif.gui import init_win32_dpi

    pyav_init_cuda_primary_context()
    init_win32_dpi()
    main()
