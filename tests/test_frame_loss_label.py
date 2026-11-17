"""
The lost-frames counter stands out as soon as a frame is lost.

Before 2026-11-17 the "Dropped: N" label was red all the time, so "Dropped: 0"
and "Dropped: 7" looked alike and an operator watching the graph could miss
the change. Now it is quiet grey at zero and a red block otherwise, and the
red stays after the run ends, until the next Start SCOS.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow
from tests.test_status_messages import _FakeCamera


@pytest.fixture
def win():
    w = MainWindow(camera=_FakeCamera())
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    w.show()
    yield w
    w._state = State.IDLE
    w.close()


def _is_alarm(w: MainWindow) -> bool:
    return w.lbl_dropped.styleSheet() == MainWindow._FRAME_LOSS_SOME_SS


def test_quiet_at_launch(win):
    assert win.lbl_dropped.text() == "Dropped: 0"
    assert not _is_alarm(win)
    assert "background" not in win.lbl_dropped.styleSheet()


def test_red_block_when_the_queue_dropped_a_frame(win):
    win._show_frame_loss(1, 0)
    assert win.lbl_dropped.text() == "Dropped: 1"
    assert _is_alarm(win)
    assert "background-color: #cc0000" in win.lbl_dropped.styleSheet()


def test_red_block_when_only_the_camera_lost_a_frame(win):
    win._show_frame_loss(0, 3)
    assert win.lbl_dropped.text() == "Dropped: 0 + 3 lost at camera"
    assert _is_alarm(win)


def test_quiet_again_when_the_count_is_reset(win):
    win._show_frame_loss(5, 2)
    win._show_frame_loss(0, 0)        # what Start SCOS does
    assert win.lbl_dropped.text() == "Dropped: 0"
    assert not _is_alarm(win)


def test_end_of_run_shows_the_final_count(win):
    """The label is refreshed from results once a second; a frame lost in the
    last second must still be on it after the run."""

    class _Pipeline:
        dropped_count = 4
        frames_lost_at_t0 = 0

    real_pipeline = win._scos_worker
    win._scos_worker = _Pipeline()
    win.camera.frames_lost = 2
    win._recorder = None
    try:
        win._write_frame_accounting()
        assert win.lbl_dropped.text() == "Dropped: 4 + 2 lost at camera"
        assert _is_alarm(win)
    finally:
        win._scos_worker = real_pipeline     # so close() stops the real one
        del win.camera.frames_lost
