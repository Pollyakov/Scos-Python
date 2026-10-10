"""
The main window fits the rig PC's screen with every field visible (todo U3, U4).

Found at the first rig session (2026-10-08). The right-hand control panel,
one field per row, needed 861 px of height and the whole window 901; the rig
screen (1280 × 800 at 150 % scaling, less the taskbar) has 752. main.py opens
the window maximized, which squeezed every field below its minimum height so
they overlapped (U4). Un-maximized, the window could not be made shorter than
931 px with its title bar, so the status bar, where warnings appear, was off
the bottom of the screen (U3).

The numbers below are the rig PC's: this is the rig PC, and the sizes are
in Qt's logical pixels at its 150 % scaling. Font and scaling change them
slightly on another machine.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow, _ControlsScrollArea
from tests.test_status_messages import _FakeCamera

# Free screen area on the rig PC (QScreen.availableGeometry(), 2026-10-10).
RIG_AVAILABLE_W = 1280
RIG_AVAILABLE_H = 752
# Title bar + borders Windows adds to the window (759 − 729, measured maximized).
RIG_FRAME_H = 30
# Inside height of the maximized window on the rig PC.
RIG_MAXIMIZED_INSIDE_H = 729


def _settle() -> None:
    """Let a size change climb the layouts — label, box, panel, window —
    which takes one pass of the event loop per level."""
    for _ in range(6):
        _app.processEvents()


@pytest.fixture
def win():
    # Shown, because Qt does not recompute the sizes of a window that has
    # never been shown: a longer label would leave every hint unchanged. Not
    # drawn on screen, though — the suite runs in the pre-commit hook.
    w = MainWindow(camera=_FakeCamera())
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    w.show()
    _settle()
    yield w
    w._state = State.IDLE
    w.close()


def _with_long_texts(w: MainWindow) -> None:
    """The longest texts the labels show in a real session, with the
    parameter boxes in their locked look, as during a measurement."""
    w._set_params_enabled(False)
    w._frame_info_label.setText("Frame #123456  min=0  max=4095")
    w.lbl_dropped.setText("Dropped: 12 + 345 lost at camera")
    w.lbl_size.setText("Size : 1936×1216")
    w.lbl_roi.setText("ROI  : full frame (1936x1216)")
    w._time_left_label.show()
    w._time_left_label.setText("⏱ 239:59 remaining")
    w._calib_label.setText(
        "Normalizing — baseline 3.2 / 5 s (percentile5) — keep the subject still"
    )
    w.image_widget.update_frame(np.zeros((1216, 1936), np.uint16))
    _settle()


def _controls(w: MainWindow) -> _ControlsScrollArea:
    return w.centralWidget().layout().itemAt(1).widget()


def test_window_minimum_fits_rig_screen(win):
    _with_long_texts(win)
    hint = win.minimumSizeHint()
    assert hint.height() + RIG_FRAME_H <= RIG_AVAILABLE_H
    assert hint.width() <= RIG_AVAILABLE_W


def test_control_panel_fits_maximized_window_without_scrolling(win):
    """Not just "fits thanks to the scroll area": the panel itself must fit,
    so Start SCOS, Status and Info are on screen without scrolling."""
    _with_long_texts(win)
    margins = win.centralWidget().layout().contentsMargins()
    room = (RIG_MAXIMIZED_INSIDE_H - win.statusBar().sizeHint().height()
            - margins.top() - margins.bottom())
    panel = _controls(win).widget()
    assert panel.minimumSizeHint().height() <= room


def test_normal_size_fits_this_screen(win):
    """"Restore Down" brings back a window that fits the screen and is tall
    enough for the control panel. (The fixture shows it un-maximized.)"""
    avail = win.screen().availableGeometry()
    assert win.height() + RIG_FRAME_H <= avail.height()
    assert win.width() <= avail.width()
    assert not _controls(win).verticalScrollBar().isVisible()


def test_scroll_area_widens_with_the_panel(win):
    """No horizontal scrollbar, so the area must follow the panel's width —
    otherwise a longer label would be clipped instead of shown."""
    area = _controls(win)
    root = win.centralWidget().layout()
    _settle()
    before = root.itemAt(1).minimumSize().width()
    # Wider than the Camera box, which sets the panel width otherwise.
    win.lbl_dropped.setText("Dropped: " + "9" * 80)
    _settle()
    after = root.itemAt(1).minimumSize().width()
    assert after > before
    assert after >= area.widget().minimumSizeHint().width()
