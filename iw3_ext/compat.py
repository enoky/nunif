"""
Compatibility guards for attaching to the stock iw3 GUI.

iw3_ext hooks into iw3 by subclassing its widgets rather than editing them, which
means an upstream rename shows up as an AttributeError from somewhere deep inside
wx. The checks here run at attach time and say which attribute went missing and
what it was needed for.
"""


class UpstreamAPIError(RuntimeError):
    pass


GUI_MODULE_API = (
    ("MainFrame", "the frame subclassed to add the Live Preview button"),
    ("IW3App", "the wx.App instantiated by iw3.gui.main()"),
    ("main", "reused so language, preset and DPI handling stay identical to the stock GUI"),
    ("T", "translation lookup"),
    ("CONFIG_DIR", "directory where iw3_ext keeps its own settings"),
)

MAIN_FRAME_API = (
    ("pnl_process", "the bottom panel that the Live Preview button is inserted into"),
    ("btn_start", "used to position the Live Preview button, and to block Start while a preview renders"),
    ("parse_args", "reads the current settings so the preview matches what Start would produce"),
    ("pnl_file", "supplies the current input path"),
    ("processing", "tells the preview whether a conversion job is already running"),
    ("depth_model", "depth model cache shared between the main window and the preview"),
    ("update_start_button_state", "hook point for enabling/disabling the Live Preview button"),
)


def _require(obj, target_name, api):
    missing = [(name, why) for name, why in api if not hasattr(obj, name)]
    if not missing:
        return
    details = "\n".join(f"  {target_name}.{name} -- needed for {why}" for name, why in missing)
    raise UpstreamAPIError(
        f"iw3_ext is not compatible with this version of iw3.\n"
        f"The following no longer exist:\n{details}\n"
        f"Upstream iw3 has most likely been refactored. Update iw3_ext to match,\n"
        f"or use the stock GUI (python -m iw3.gui) until it is updated."
    )


def check_gui_module(module):
    _require(module, "iw3.gui", GUI_MODULE_API)


def check_main_frame(frame):
    _require(frame, "MainFrame", MAIN_FRAME_API)
    if frame.pnl_process.GetSizer() is None:
        raise UpstreamAPIError(
            "iw3_ext is not compatible with this version of iw3.\n"
            "MainFrame.pnl_process has no sizer, so the Live Preview button cannot be placed."
        )
