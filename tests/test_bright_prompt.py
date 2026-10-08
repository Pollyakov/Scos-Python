"""
The bright-calibration prompt keeps the subject in place.

The bright frames are taken with the subject in the measurement area — the
user's instruction of 2026-10-08, and how the MATLAB reference was made:
`smoothingCoefficients.mat` (spIm, spVar) for the lab recording was computed
from that recording's own frames, subject included, and our calibration from
the same frames reproduces MATLAB's corrected κ² to within 2 %
(`tests/test_bright_cal_offline.py`). The prompt used to say "remove the
subject", which `SCOS_protocol.md` never asked for.

It also matters for the baseline: the measurement starts by itself when this
calibration ends, and its first seconds become the normalization constant.
With the subject already in place there is nothing to put back during them.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

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


@pytest.fixture
def bright_prompt(monkeypatch):
    """Open the bright prompt, answer Cancel, return (title, text)."""
    seen = []

    def _question(parent, title, text, *a, **k):
        seen.append((title, text))
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    w = MainWindow(camera=_StubCamera())
    try:
        w._start_bright_cal()
    finally:
        w.close()
    assert len(seen) == 1
    return seen[0]


def test_the_title_is_unchanged(bright_prompt):
    # tools/rehearsal.py answers dialogs by title, and the dialog-order test
    # in test_recording_name.py identifies them the same way.
    assert bright_prompt[0] == "Calibration — Step 2 of 2: Bright Frames"


def test_it_asks_for_the_laser_on(bright_prompt):
    assert "turn on the laser" in bright_prompt[1].lower()


def test_it_keeps_the_subject_in_place(bright_prompt):
    text = bright_prompt[1].lower()
    assert "keep the subject" in text
    assert "remove" not in text, (
        "the bright calibration is taken with the subject in place")


def test_it_warns_that_the_measurement_follows_at_once(bright_prompt):
    # No further pop-up comes between this calibration and the measurement.
    assert "measurement starts automatically" in bright_prompt[1].lower()
