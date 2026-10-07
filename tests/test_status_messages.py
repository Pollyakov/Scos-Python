"""
Status-bar messages stay on screen (todo K1, K4).

Every displayed frame used to write "Frame #… | shape=… min=… max=…" to the
status bar with showMessage(), up to 30 times a second outside a measurement
and every 2.5 s inside one. Whatever else was posted there was gone almost at
once: the session folder, the closing "Session finished → … | laser-off note"
(the only on-screen trace of a skipped laser-off check) and the overload
warning. The frame readout now has its own permanent label, so a message stays
until another event replaces it.

The headless rehearsal checks the same thing on the real flow:
`--scenario slowdown` for the overload warning, every scenario for the closing
message.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow


class _FakeCamera(QObject):
    frame_ready   = pyqtSignal(np.ndarray, float)
    display_ready = pyqtSignal(np.ndarray)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)

    pixel_format  = "Mono12"
    exposure_us   = 8000
    gain_db       = 8.0
    frame_rate    = 20.0
    trigger_mode  = "Off"
    trigger_delay = 0

    def set_trigger(self, *a, **k):   pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k): pass
    def stop(self, *a, **k):          pass
    def close(self, *a, **k):         pass
    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


@pytest.fixture
def win():
    w = MainWindow(camera=_FakeCamera())
    yield w
    w.close()


def _frame(value=100):
    return np.full((16, 16), value, dtype=np.uint16)


class TestFramesDoNotOverwriteMessages:

    def test_a_message_survives_preview_frames(self, win):
        win._set_state(State.PREVIEW)
        win.status.showMessage("Session folder: C:/data/subject_1")

        for _ in range(5):
            win._on_display_frame(_frame())

        assert win.status.currentMessage() == "Session folder: C:/data/subject_1"

    def test_the_frame_readout_still_updates(self, win):
        win._set_state(State.PREVIEW)
        win._on_display_frame(_frame(7))
        win._on_display_frame(_frame(9))

        text = win._frame_info_label.text()
        assert text.startswith(f"Frame #{win._frame_count}") and "max=9" in text

    def test_the_overload_warning_survives_measurement_frames(self, win):
        """K4: during a measurement the frame text was rewritten every 2.5 s."""
        win._set_state(State.MEASURING)
        win._on_overload(16)
        warning = win.status.currentMessage()
        assert "overload" in warning.lower()

        for _ in range(3):
            win._last_display_time = 0.0     # let each frame past the 2.5 s limit
            win._on_display_frame(_frame())

        assert win.status.currentMessage() == warning

    def test_the_overload_warning_says_when_it_happened(self, win):
        # It stays up after the machine has recovered, so it must read as an
        # event at a time, not as the current state.
        win._on_overload(16)
        assert "(at " in win.status.currentMessage()

    def test_the_closing_message_survives_frames_after_stop(self, win):
        """K1: the laser-off note rides on this message and must not vanish."""
        win._state = State.MEASURING
        win.btn_start_scos.blockSignals(True)
        win.btn_start_scos.setChecked(True)
        win.btn_start_scos.blockSignals(False)
        # The laser-off prompt is a modal dialog, which tests block; the check
        # then reports that it could not run — exactly the kind of note the
        # operator must be able to read after the session.
        win.btn_start_scos.setChecked(False)
        closing = win.status.currentMessage()
        assert closing.startswith("Session finished")
        assert "laser-off check could not run" in closing

        for _ in range(5):
            win._on_display_frame(_frame())

        assert win.status.currentMessage() == closing
