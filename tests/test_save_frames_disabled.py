"""
The "Save Frames" checkbox stays greyed out until raw-frame saving is ready (todo.md F1).

A ticked box writes every raw frame to disk — ~1 MB per 700x700 frame, ~70 GB an
hour at 20 Hz — synchronously on the GUI thread (F4) and with no disk-space check
in front of it (B2). Nothing else a session writes comes close to filling a disk
(a 3-hour run is under 50 MB), so disabling the box is what removes that risk for
the first rig sessions. It stays *visible*: the protocol lists it among the SCOS
parameters (docs/SCOS_protocol.md:9).
"""

import sys
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow


class _StubCamera(QObject):
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

    def __init__(self):
        super().__init__()
        self.frames_emitted = 0

    def set_trigger(self, *a, **k):      pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass
    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


def test_save_frames_disabled_and_unchecked_at_startup():
    w = MainWindow(camera=_StubCamera())
    try:
        assert not w.chk_save_frames.isEnabled()
        assert not w.chk_save_frames.isChecked()
        assert not w.chk_save_frames.isHidden()   # the protocol lists it
    finally:
        w.close()


def test_nothing_in_a_session_re_enables_save_frames():
    """Unlocking the parameters or passing through every state must not bring it back."""
    w = MainWindow(camera=_StubCamera())
    try:
        w._set_params_enabled(False)
        w._set_params_enabled(True)
        for state in State:
            w._set_state(state)
            assert not w.chk_save_frames.isEnabled(), state.name
    finally:
        w.close()
