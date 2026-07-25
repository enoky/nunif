# iw3_ext

Additions to `iw3-gui` that live outside the nunif source tree.

Nothing here modifies an upstream file. The GUI additions are installed at runtime
by subclassing `iw3.gui.MainFrame`, so pulling new upstream commits never conflicts.

## Running

```
python -m iw3_ext.gui
```

or double-click `iw3-gui-plus.bat`. The stock `iw3-gui.bat` still works and is
unaffected (it just does not show the extra button).

## Keeping the fork in sync

`master` and `dev` are kept as untouched mirrors of upstream, all work happens on
a feature branch that only adds files:

```
git fetch upstream
git checkout dev && git merge --ff-only upstream/dev
git checkout live-preview && git rebase dev
```

This check must print nothing, i.e. no upstream file has been modified:

```
git diff --stat dev -- . ":(exclude)iw3_ext"
```

## Status

| Phase | Content | State |
| --- | --- | --- |
| 1 | Button injection, preview window shell, launcher | done |
| 2 | Render pipeline, image input, worker thread, canvas, view modes, save | done |
| 3 | Video input and seek bar | done |
| 4 | Auto-refresh on main-window setting changes, preview-only depth model | done |
| 5 | Persisted window state, translations | done |
| 6 | Previewing with the VideoDepthAnything models | done |
| 7 | Timeline scrubbing | done |

With `Auto` on, the window watches the main window and re-renders about half a
second after a setting stops changing. It does that by walking the controls
(`settings_watcher.py`, ~0.3 ms) rather than calling `parse_args()`, which costs
~240 ms per call and pops a modal dialog on a half-typed value. Auto-refresh
suppresses that dialog while it reads the settings, so a value being typed can
never put one on screen.

`Depth` selects a preview-only depth model, for iterating with a small model
before converting with a large one. It never reaches the main window: an
override is cached separately and `Start` keeps using the model you chose there.

Every model is offered, including the VideoDepthAnything ones that report
`is_image_supported() == False`. How they render one frame, and why the window
the online variant needs is free, is at the top of `vda.py`. Measured on a
320x240 clip, warm (excluding the first render, which loads the model):

| | |
| --- | --- |
| `Distill_Any_S` (image model) | 35 ms |
| `VDA_Stream_S` | 29 ms |
| `VDA_S`, 31 warmup frames | 185 ms |
| `VDA_S`, no warmup frames | 172 ms |

The 31 frames of real temporal context cost 13 ms, all of it decoding: the
model runs the same single window pass either way. VDA output is an
approximation rather than the byte-exact frame the image models give, and the
window says so in the status bar.

Window size, position and the toolbar settings are kept in
`<config dir>/iw3-gui-preview.json`, separate from the main window's preset file
for the reason given at the top of `preview_frame.py`. A damaged state file is
ignored, and a position on a display that is no longer connected is dropped.
Restoring `Auto` as on does not render on open: it takes a real setting change.

Strings the preview adds are translated in `locales/`, falling back to iw3's own
table (so `Depth`, `Save` and friends match the rest of the GUI) and then to
English. English and Japanese are filled in; adding a language means dropping a
`.yml` next to the others.

The rendered frame is byte-identical to what `Start` writes for that frame: a
256x256 test image rendered through the preview and through `python -m iw3` with
the same settings compared at a max absolute difference of 0. Video frames are
decoded through the same path a conversion uses, verified against `hook_frame()`
at several seek positions, also at a max absolute difference of 0.

## Scrubbing

One slider tick is one frame once the frame rate is known, so the arrow keys
step a frame at a time and the label shows the frame index. Dragging updates the
label immediately and renders once the slider settles, whether or not `Auto` is
on: moving the timeline is an explicit request for another frame.

| | before | after |
| --- | --- | --- |
| GUI thread per drag event | 175 ms | 0.26 ms |
| reading the settings for a render | 101 ms | 0.27 ms |
| decoding one frame forward | 1.1 ms | 0.2 ms |

`parse_args()` costs about 175 ms, so calling it per slider tick made a drag
unusable. It is now reparsed only when the settings snapshot actually changes,
and the decoder runs on rather than seeking when the next frame is just ahead,
which avoids re-decoding from the previous keyframe. A backward jump still
seeks, and still lands on the exact frame.

For video the slider spans the Start/End time range when one is set. Three things
a still preview cannot reproduce are listed at the top of `video_source.py`:
Max FPS frame dropping, Auto Crop analysis over the whole file, and Flicker
Reduction, which works across frames (the window says so when it is on).

## Layout

| File | Role |
| --- | --- |
| `gui.py` | Entry point. Subclasses `MainFrame`, inserts the Live Preview button, owns the preview window lifetime. |
| `preview_frame.py` | The Live Preview window: toolbar, canvas, seek bar, status bar, render requests. |
| `pipeline.py` | Mirror of the model setup in `iw3_main()`, plus the view-mode handling. |
| `frame_source.py` | Turns the input path into the single frame that gets rendered. |
| `video_source.py` | Seeks a video and decodes one frame the way a conversion would. |
| `vda.py` | Renders a frame with the temporal VideoDepthAnything models. |
| `render_worker.py` | Background render thread with a one-slot request queue. |
| `model_cache.py` | Keeps the stereo model, and any preview-only depth model, loaded between renders. |
| `settings_watcher.py` | Cheap change detection over the main window's controls, for `Auto`. |
| `window_state.py` | The preview window's own settings file. |
| `locales/` | Translations for the strings the preview adds. |
| `image_canvas.py` | Fit/100% zoom, wheel zoom, drag pan. |
| `compat.py` | Checks the upstream attributes this package depends on, and reports a readable error if iw3 changes. |

`pipeline.py` and `compat.py` are the only files coupled to iw3 internals. If an
upstream update breaks something, look there first.
