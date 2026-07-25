import wx


MIN_ZOOM = 0.05
MAX_ZOOM = 8.0
ZOOM_STEP = 1.25


def pil_to_wx_image(im):
    rgb = im.convert("RGB")
    wx_image = wx.Image(rgb.width, rgb.height)
    wx_image.SetData(rgb.tobytes())
    return wx_image


class ImageCanvas(wx.Panel):
    """Displays one image with fit/100% zoom, wheel zoom and drag pan."""

    def __init__(self, parent):
        super().__init__(parent, style=wx.FULL_REPAINT_ON_RESIZE)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.image = None
        self.message = ""
        self.zoom = 1.0
        self.fit_mode = True
        self.offset_x = 0
        self.offset_y = 0
        self.drag_origin = None
        self.scaled_bitmap = None
        self.scaled_zoom = None

        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)
        self.Bind(wx.EVT_MOUSEWHEEL, self.on_mouse_wheel)
        self.Bind(wx.EVT_LEFT_DOWN, self.on_left_down)
        self.Bind(wx.EVT_LEFT_UP, self.on_left_up)
        self.Bind(wx.EVT_MOTION, self.on_motion)
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self.on_capture_lost)

    # state

    def set_image(self, image, keep_view=False):
        previous = self.image
        self.image = image
        self.message = ""
        self._drop_cache()
        size_changed = (
            previous is None or image is None or
            previous.GetWidth() != image.GetWidth() or previous.GetHeight() != image.GetHeight()
        )
        if not keep_view or size_changed:
            self.fit_to_window()
        else:
            self._clamp_offset()
            self.Refresh()

    def clear(self):
        self.image = None
        self._drop_cache()
        self.Refresh()

    def set_message(self, message):
        self.message = message
        self.Refresh()

    def has_image(self):
        return self.image is not None

    def get_zoom(self):
        return self.zoom

    # view

    def fit_to_window(self):
        self.fit_mode = True
        self.zoom = self._fit_zoom()
        self._center()
        self.Refresh()

    def zoom_100(self):
        self.zoom_to(1.0)

    def zoom_to(self, zoom, center=None):
        if self.image is None:
            return
        zoom = min(max(zoom, MIN_ZOOM), MAX_ZOOM)
        if center is None:
            client = self.GetClientSize()
            center = wx.Point(client.width // 2, client.height // 2)

        # keep the point under the cursor in place
        image_x = (center.x - self.offset_x) / self.zoom
        image_y = (center.y - self.offset_y) / self.zoom
        self.zoom = zoom
        self.fit_mode = False
        self.offset_x = int(center.x - image_x * zoom)
        self.offset_y = int(center.y - image_y * zoom)
        self._clamp_offset()
        self.Refresh()

    def _fit_zoom(self):
        if self.image is None:
            return 1.0
        client = self.GetClientSize()
        if client.width <= 0 or client.height <= 0:
            return 1.0
        zoom = min(client.width / self.image.GetWidth(), client.height / self.image.GetHeight())
        return min(max(zoom, MIN_ZOOM), 1.0)

    def _scaled_size(self):
        return (max(1, int(self.image.GetWidth() * self.zoom)),
                max(1, int(self.image.GetHeight() * self.zoom)))

    def _center(self):
        if self.image is None:
            return
        client = self.GetClientSize()
        width, height = self._scaled_size()
        self.offset_x = (client.width - width) // 2
        self.offset_y = (client.height - height) // 2

    def _clamp_offset(self):
        if self.image is None:
            return
        client = self.GetClientSize()
        width, height = self._scaled_size()
        if width <= client.width:
            self.offset_x = (client.width - width) // 2
        else:
            self.offset_x = min(0, max(client.width - width, self.offset_x))
        if height <= client.height:
            self.offset_y = (client.height - height) // 2
        else:
            self.offset_y = min(0, max(client.height - height, self.offset_y))

    def _drop_cache(self):
        self.scaled_bitmap = None
        self.scaled_zoom = None

    def _get_bitmap(self):
        if self.scaled_bitmap is not None and self.scaled_zoom == self.zoom:
            return self.scaled_bitmap
        width, height = self._scaled_size()
        if width == self.image.GetWidth() and height == self.image.GetHeight():
            bitmap = self.image.ConvertToBitmap()
        else:
            bitmap = self.image.Scale(width, height, wx.IMAGE_QUALITY_HIGH).ConvertToBitmap()
        self.scaled_bitmap = bitmap
        self.scaled_zoom = self.zoom
        return bitmap

    # events

    def on_paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        if self.image is not None:
            dc.DrawBitmap(self._get_bitmap(), self.offset_x, self.offset_y, False)
        elif self.message:
            dc.SetTextForeground(self.GetForegroundColour())
            client = self.GetClientSize()
            text_width, text_height = dc.GetTextExtent(self.message)
            dc.DrawText(self.message,
                        (client.width - text_width) // 2,
                        (client.height - text_height) // 2)

    def on_size(self, event):
        if self.fit_mode:
            self.zoom = self._fit_zoom()
            self._center()
        else:
            self._clamp_offset()
        self.Refresh()
        event.Skip()

    def on_mouse_wheel(self, event):
        if self.image is None:
            return
        rotation = event.GetWheelRotation()
        if rotation == 0:
            return
        factor = ZOOM_STEP if rotation > 0 else 1.0 / ZOOM_STEP
        self.zoom_to(self.zoom * factor, center=event.GetPosition())

    def on_left_down(self, event):
        if self.image is None:
            return
        self.drag_origin = (event.GetPosition(), self.offset_x, self.offset_y)
        if not self.HasCapture():
            self.CaptureMouse()
        self.SetCursor(wx.Cursor(wx.CURSOR_SIZING))

    def on_left_up(self, event):
        self._end_drag()

    def on_capture_lost(self, event):
        self.drag_origin = None
        self.SetCursor(wx.NullCursor)

    def _end_drag(self):
        self.drag_origin = None
        if self.HasCapture():
            self.ReleaseMouse()
        self.SetCursor(wx.NullCursor)

    def on_motion(self, event):
        if self.drag_origin is None or not event.Dragging() or not event.LeftIsDown():
            return
        origin, offset_x, offset_y = self.drag_origin
        position = event.GetPosition()
        self.fit_mode = False
        self.offset_x = offset_x + (position.x - origin.x)
        self.offset_y = offset_y + (position.y - origin.y)
        self._clamp_offset()
        self.Refresh()
