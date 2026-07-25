"""
iw3_ext: additions to iw3-gui that live outside the nunif tree.

Nothing in this package modifies upstream files. The GUI additions are installed
at runtime by subclassing iw3.gui.MainFrame, so `git rebase upstream/dev` stays
conflict-free.

Run with:
    python -m iw3_ext.gui
"""
