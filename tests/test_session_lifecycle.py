"""
Tests for the session lifecycle in gui/main_window.py — merged_worklist task 8.

Two things are covered:

  1. **The FINISHED transition.** Before this task, Stop SCOS went straight to
     PREVIEW and FINISHED was only ever reached by HDF5 replay — so there was no
     single point where a measurement is finalized, and tasks 9-12 (write rBFi,
     laser-off check, save figure) had nowhere to attach. Both the Stop button
     and the duration auto-stop must now pass through FINISHED. Cancelling an
     unfinished *calibration* must NOT, since there are no results to finalize.

  2. **One session folder.** The second QFileDialog that used to open inside
     _start_recorder — mid-measurement, after calibration — is gone. Everything
     goes to the folder created at Start SCOS.
"""

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QFileDialog
from PyQt6.QtCore import QObject, pyqtSignal

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow


class _FakeCamera(QObject):
    """Minimal stand-in for CameraThread — signals and no-op controls only.

    frame_ready carries (frame, capture_time) since task 5 moved timestamping
    to the capture site.
    """

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

    def set_trigger(self, *a, **k):      pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass
    def get_info(self):                  return {}


@pytest.fixture
def win(qtbot_free=None):
    """A MainWindow backed by a fake camera, with state transitions recorded."""
    w = MainWindow(camera=_FakeCamera())
    w._state_log = []
    original = w._set_state

    def _spy(new_state):
        w._state_log.append(new_state)
        original(new_state)

    w._set_state = _spy
    yield w
    w.close()


def _arm_measuring(w, state=State.MEASURING):
    """Put the window into a running-measurement state without the real flow."""
    w._state = state
    # Button must read as checked so that un-checking it emits toggled(False).
    w.btn_start_scos.blockSignals(True)
    w.btn_start_scos.setChecked(True)
    w.btn_start_scos.blockSignals(False)


class TestFinishedTransition:

    def test_stop_button_passes_through_finished_then_preview(self, win):
        """Stop SCOS during a measurement must finalize via FINISHED."""
        _arm_measuring(win)
        win.btn_start_scos.setChecked(False)   # emits toggled → _toggle_scos(False)

        assert State.FINISHED in win._state_log, (
            f"FINISHED was never entered; transitions were {win._state_log}. "
            "Tasks 9-12 have nothing to hook into."
        )
        assert win._state_log[-1] == State.PREVIEW, (
            f"expected to settle in PREVIEW, ended in {win._state_log[-1]}"
        )
        assert win._state_log.index(State.FINISHED) < len(win._state_log) - 1, (
            "FINISHED must come before PREVIEW, not after"
        )

    def test_auto_stop_passes_through_finished(self, win):
        """The duration auto-stop must finalize identically to the button.

        Auto-stop works by un-checking the button from inside _on_scos_result.
        Several other places in this file wrap that call in blockSignals(), which
        would silently skip _toggle_scos entirely — so assert on the state, not
        on the button.
        """
        _arm_measuring(win)
        win._measuring_start_time   = time.time() - 100.0
        win._measurement_duration_s = 1.0        # already exceeded

        win._on_scos_result(t=1.0, k2_raw=0.05, k2_corr=0.04,
                            mean_i=100.0, proc_ms=5.0)

        assert State.FINISHED in win._state_log, (
            f"auto-stop did not pass through FINISHED; got {win._state_log}"
        )
        assert win._state_log[-1] == State.PREVIEW

    def test_cancelling_dark_cal_never_reaches_finished(self, win):
        """Abandoning calibration has no results — it must not be 'finished'."""
        _arm_measuring(win, state=State.DARK_CAL)
        win.btn_start_scos.setChecked(False)

        assert State.FINISHED not in win._state_log, (
            "cancelling calibration wrongly finalized a session that never "
            f"produced data; transitions were {win._state_log}"
        )
        assert win._state_log[-1] == State.PREVIEW

    def test_cancelling_bright_cal_never_reaches_finished(self, win):
        _arm_measuring(win, state=State.BRIGHT_CAL)
        win.btn_start_scos.setChecked(False)

        assert State.FINISHED not in win._state_log
        assert win._state_log[-1] == State.PREVIEW


class TestSessionFolder:

    def test_start_recorder_opens_no_dialog(self, win, tmp_path, monkeypatch):
        """Regression test for the second, mid-measurement folder dialog.

        Any attempt to open one now raises, so the test fails loudly instead of
        hanging on a modal dialog that nothing will ever click.
        """
        def _boom(*a, **k):
            raise AssertionError(
                "_start_recorder opened a folder dialog — it must use the "
                "session folder chosen at Start SCOS"
            )

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", _boom)

        win._session_folder = tmp_path
        win.processor.dark_mean  = np.zeros((8, 8))
        win.processor.dark_var   = np.zeros((8, 8))
        win.processor.bright_var = np.zeros((8, 8))
        win._mask = np.ones((8, 8), dtype=bool)

        win._start_recorder()

        assert win._recorder is not None, "recorder was not opened"
        assert Path(win._recorder.path).parent == tmp_path, (
            f"results went to {Path(win._recorder.path).parent}, "
            f"expected the session folder {tmp_path}"
        )
        win._stop_recorder()

    def test_no_session_folder_means_no_recorder(self, win, monkeypatch):
        """Cancelling the folder choice still lets the measurement run, unsaved.

        This preserves the old behaviour of cancelling the dialog rather than
        turning it into a hard failure.
        """
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no dialog expected")),
        )
        win._session_folder = None
        win._start_recorder()
        assert win._recorder is None

    def test_finish_session_releases_folder_but_keeps_root(self, win, tmp_path):
        """The next run must create its own folder, but must not re-ask for the
        parent — a stale _session_folder would silently collect the next run's
        calibration files into this run's directory."""
        win._output_root    = tmp_path
        win._session_folder = tmp_path / "scos_20260907_120000"
        win._session_folder.mkdir()

        win._finish_session()

        assert win._session_folder is None, "session folder was not released"
        assert win._output_root == tmp_path, "parent folder should be remembered"
