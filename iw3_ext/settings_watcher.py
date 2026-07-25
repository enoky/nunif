"""
Change detection for the main window.

parse_args() looks like the obvious way to snapshot the settings, but it costs
about 240 ms per call here (it builds an argparse parser and calls gc_collect())
and it pops a modal dialog on a half-typed value, so it cannot run on a timer.
Walking the controls instead costs about 0.3 ms and needs no knowledge of which
widgets exist, so a control added upstream is picked up on its own.
"""


# controls whose value cannot change the rendered frame
IGNORED_MAIN_FRAME_WIDGETS = ("prg_tqdm", "cbo_language", "cbo_app_preset")
IGNORED_FILE_PANEL_WIDGETS = ("output_path_widget",)


def ignored_windows(main_frame):
    ignored = set()
    for name in IGNORED_MAIN_FRAME_WIDGETS:
        widget = getattr(main_frame, name, None)
        if widget is not None:
            ignored.add(id(widget))

    file_panel = getattr(main_frame, "pnl_file", None)
    if file_panel is not None:
        for name in IGNORED_FILE_PANEL_WIDGETS:
            widget = getattr(file_panel, name, None)
            if widget is not None:
                ignored.add(id(widget))

    return ignored


def settings_snapshot(main_frame):
    """A value that changes whenever a setting that affects the output changes."""
    values = []
    _walk(main_frame, values, ignored_windows(main_frame))
    return tuple(values)


def _walk(window, values, ignored):
    for child in window.GetChildren():
        if id(child) in ignored:
            continue
        for getter in ("GetValue", "GetSelection"):
            read = getattr(child, getter, None)
            if read is None:
                continue
            try:
                value = read()
            except Exception:  # noqa
                break
            values.append(value if isinstance(value, (str, int, float, bool)) else repr(value))
            break
        _walk(child, values, ignored)
