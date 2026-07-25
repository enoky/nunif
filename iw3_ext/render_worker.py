import threading
import traceback
import torch
import wx


class RenderRequest():
    __slots__ = ("seq", "args", "input_path", "view_mode", "scale", "seek", "seek_index",
                 "override", "share", "depth_file", "depth_from_main")

    def __init__(self, seq, args, input_path, view_mode, scale, seek=0.0, seek_index=None,
                 override=None, share=None, depth_file=None, depth_from_main=False):
        self.seq = seq
        self.args = args
        self.input_path = input_path
        self.view_mode = view_mode
        self.scale = scale
        self.seek = seek
        # the frame number, when the slider counts frames
        self.seek_index = seek_index
        # preview-only depth model, and the main window's model state to restore
        self.override = override
        self.share = share or {}
        # depth map rendered outside iw3, used instead of a model, and whether
        # it came from the main window rather than being chosen here
        self.depth_file = depth_file
        self.depth_from_main = depth_from_main


class RenderWorker():
    """
    One background thread with a single pending slot: a new request replaces the
    pending one, so dragging a slider does not queue a render per tick.

    Note that a render already in flight is not interrupted -- process_image() is
    a single shot with no cancellation points. stop_event only marks the result as
    stale, so it gets discarded instead of drawn.
    """

    def __init__(self, render_fn, on_done, on_error):
        self.render_fn = render_fn
        self.on_done = on_done
        self.on_error = on_error
        self.stop_event = threading.Event()
        self.cond = threading.Condition()
        self.pending = None
        self.busy = False
        self.shutdown_requested = False
        self.thread = threading.Thread(target=self._loop, name="iw3ext-render", daemon=True)
        self.thread.start()

    def submit(self, request):
        with self.cond:
            self.pending = request
            self.stop_event.set()
            self.cond.notify()

    def cancel(self):
        with self.cond:
            self.pending = None
        self.stop_event.set()

    def is_busy(self):
        with self.cond:
            return self.busy or self.pending is not None

    def shutdown(self, timeout=30.0):
        with self.cond:
            self.shutdown_requested = True
            self.pending = None
            self.cond.notify()
        self.stop_event.set()
        self.thread.join(timeout)

    def _loop(self):
        while True:
            with self.cond:
                while self.pending is None and not self.shutdown_requested:
                    self.cond.wait()
                if self.shutdown_requested:
                    return
                request = self.pending
                self.pending = None
                self.busy = True
                self.stop_event.clear()

            try:
                result = self.render_fn(request, self.stop_event)
                self._post(self.on_done, request, result)
            except torch.cuda.OutOfMemoryError as e:
                torch.cuda.empty_cache()
                self._post(self.on_error, request, e, traceback.format_exc())
            except Exception as e:  # noqa
                self._post(self.on_error, request, e, traceback.format_exc())
            finally:
                with self.cond:
                    self.busy = False

    def _post(self, callback, *args):
        if self.shutdown_requested:
            return
        try:
            wx.CallAfter(callback, *args)
        except RuntimeError:
            # the app is shutting down
            pass
