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
| 2 | Render pipeline, image input, worker thread, image canvas | not started |
| 3 | Video seek bar | not started |
| 4 | Auto-refresh, zoom/pan, view modes, preview scale, save | not started |
| 5 | Persisted window state, translations | not started |

## Layout

| File | Role |
| --- | --- |
| `gui.py` | Entry point. Subclasses `MainFrame`, inserts the Live Preview button, owns the preview window lifetime. |
| `preview_frame.py` | The Live Preview window: toolbar, canvas, seek bar, status bar. |
| `compat.py` | Checks the upstream attributes this package depends on, and reports a readable error if iw3 changes. |

`compat.py` and (from phase 2) `pipeline.py` are the only files coupled to iw3
internals. If an upstream update breaks something, look there first.
