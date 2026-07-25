# iw3.gui is imported first on purpose: it pulls in nunif.pythonw_fix and
# nunif.gui.subprocess_patch, which have to run before anything else.
import iw3.gui as iw3_gui
import wx
from . import compat
from .preview_frame import PreviewFrame


compat.check_gui_module(iw3_gui)


def T(s):
    return iw3_gui.T(s)


def find_sizer_index(sizer, window):
    for i, item in enumerate(sizer.GetChildren()):
        if item.GetWindow() is window:
            return i
    return sizer.GetItemCount()


class PreviewMainFrame(iw3_gui.MainFrame):
    def initialize_component(self):
        super().initialize_component()
        self.preview_frame = None
        self.install_live_preview_button()

    def install_live_preview_button(self):
        compat.check_main_frame(self)
        sizer = self.pnl_process.GetSizer()
        self.btn_live_preview = wx.Button(self.pnl_process, label=T("Live Preview"),
                                          name="iw3ext_btn_live_preview")
        sizer.Insert(find_sizer_index(sizer, self.btn_start), self.btn_live_preview, 0, wx.ALL, 4)
        self.btn_live_preview.Bind(wx.EVT_BUTTON, self.on_click_btn_live_preview)
        self.pnl_process.Layout()
        self.update_start_button_state()

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
        super().on_click_btn_start(event)

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
