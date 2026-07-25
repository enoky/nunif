"""
Preview window state, kept in its own file.

The main window's controls are persisted by a wx PersistenceManager that walks
its frame tree. The preview window deliberately sits outside that tree (a child
frame would leak into the preset files, see preview_frame.py), so it keeps its
own small JSON instead.
"""
import json
from os import path
import wx
import iw3.gui as iw3_gui


STATE_PATH = path.join(iw3_gui.CONFIG_DIR, "iw3-gui-preview.json")
MIN_SAVED_SIZE = (400, 300)


def load_state():
    try:
        with open(STATE_PATH, mode="r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except Exception:  # noqa
        # a missing or damaged state file is not worth a message
        return {}


def save_state(state):
    try:
        with open(STATE_PATH, mode="w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:  # noqa
        pass


def is_valid_size(size):
    return (isinstance(size, (list, tuple)) and len(size) == 2 and
            all(isinstance(v, int) for v in size) and
            size[0] >= MIN_SAVED_SIZE[0] and size[1] >= MIN_SAVED_SIZE[1])


def is_visible_position(position):
    """Rejects a position saved on a display that is no longer connected."""
    if not (isinstance(position, (list, tuple)) and len(position) == 2 and
            all(isinstance(v, int) for v in position)):
        return False
    return wx.Display.GetFromPoint(wx.Point(position[0], position[1])) != wx.NOT_FOUND
